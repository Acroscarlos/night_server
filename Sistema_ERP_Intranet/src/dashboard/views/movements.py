#!/usr/bin/env python
# coding: utf-8

import logging
import streamlit as st
import pandas as pd
import uuid
import time
import json
from sqlalchemy import func
from datetime import datetime
from src.services.loyverse_api import LoyverseAPI
from src.database.db_core import SessionLocal
from src.database.models import Product, InventoryLocation, InventoryMovement, AuditLog, ApiOutbox
from src.logger_config import get_custom_logger
from src.services.inventory_logic import registrar_movimiento_kardex
seguridad_logger = get_custom_logger("seguridad", "seguridad_auditoria.log")
inv_logger = get_custom_logger("inventario", "modulo_inventario.log")

def _get_current_user():
    return st.session_state.get("username", "sistema")

def _get_stock_for_location(product_id: str, loc_id: int) -> float:
    """Consulta rápida de stock disponible en una ubicación (CQRS)."""
    # ⚠️ Nota: product_id ahora es tipo 'str' porque tu tabla Product usa UUIDs (String)
    db = SessionLocal()
    try:
        from src.database.models import ProductStockLocation # Importación de seguridad
        stock_loc = db.query(ProductStockLocation).filter_by(
            product_id=product_id, location_id=loc_id
        ).first()
        
        return stock_loc.stock_actual if stock_loc else 0.0
    finally:
        db.close()

def _register_batch_movements(cart_items: list, src_id: int, dest_id: int, m_type: str, status: str) -> bool:
    """Traspaso atómico con validaciones preventivas, manejo TOCTOU y Outbox Idempotente."""
    
    # 🛡️ ESCUDO 1 (Pre-Transaccional): Nunca confiamos ciegamente en la UI
    if src_id and dest_id and src_id == dest_id:
        st.error("🚨 Origen y Destino no pueden ser el mismo almacén.")
        return False

    for item in cart_items:
        if float(item['qty']) <= 0:
            st.error(f"🚨 Falla de integridad: Cantidad inválida (<=0) detectada en el SKU {item['sku']}.")
            return False

    db = SessionLocal()
    try:
        user = _get_current_user()
        
        # 🛡️ ESCUDO 2: Creamos un ID único de transacción para todo el lote (Idempotencia)
        reference_hash = str(uuid.uuid4())
        
        src_loc = db.query(InventoryLocation).filter_by(id=src_id).first() if src_id else None
        dest_loc = db.query(InventoryLocation).filter_by(id=dest_id).first() if dest_id else None

        for item in cart_items:
            sku = item['sku']
            prod_id = item['product_id']
            qty = float(item['qty'])

            # 1. EVENT SOURCING: Tu lógica intacta
            if src_id:
                registrar_movimiento_kardex(db, prod_id, src_id, -qty, m_type, user)
            if dest_id:
                registrar_movimiento_kardex(db, prod_id, dest_id, qty, m_type, user)

            # 2. TRANSACTIONAL OUTBOX: Tu lógica intacta, solo le inyectamos el reference_id
            if src_loc and src_loc.loyverse_store_id:
                payload_src = {
                    "sku": sku, 
                    "store_id": src_loc.loyverse_store_id, 
                    "qty_delta": -qty,
                    "reference_id": reference_hash # <- Inyección de rastreo
                }
                db.add(ApiOutbox(target_system="LOYVERSE", endpoint="adjust_inventory", payload_json=json.dumps(payload_src)))
                
            if dest_loc and dest_loc.loyverse_store_id:
                payload_dest = {
                    "sku": sku, 
                    "store_id": dest_loc.loyverse_store_id, 
                    "qty_delta": qty,
                    "reference_id": reference_hash # <- Inyección de rastreo
                }
                db.add(ApiOutbox(target_system="LOYVERSE", endpoint="adjust_inventory", payload_json=json.dumps(payload_dest)))

        # 3. COMMIT ATÓMICO: Si se va la luz aquí, no se guarda ni el stock ni la tarea. 
        # Si pasa, ambos se guardan garantizando consistencia absoluta.
        db.commit()
        return True

    except ValueError as ve:
        # 🛡️ ESCUDO 3 (Manejo TOCTOU): Si el Kardex rebota la operación por stock insuficiente en caliente
        db.rollback()
        inv_logger.warning(f"Conflicto de stock TOCTOU: {str(ve)}")
        st.warning(f"⚠️ Operación abortada: Alguien movió el inventario mientras tenías el carrito abierto. {str(ve)}", icon="⏳")
        return False

    except Exception as e:
        # Tu manejo de errores generales intacto
        db.rollback()
        inv_logger.error(f"Fallo al registrar traspaso. DB Intacta. Detalle: {str(e)}", exc_info=True)
        st.error(f"❌ Error procesando el traspaso: {str(e)}")
        return False
    finally:
        db.close()

def render_movements_view():
    """Vista de UI para la gestión de traspasos por lote."""
    st.markdown("### 🚚 Movimientos y Entregas")
    st.markdown("#### 🛒 Carrito de Traspasos por Lote")

    if "transfer_cart" not in st.session_state:
        st.session_state.transfer_cart = []

    db = SessionLocal()
    try:
        productos = db.query(Product).all()
        locs = db.query(InventoryLocation).all()

        if not productos or not locs:
            st.warning("⚠️ Faltan productos o ubicaciones en el catálogo.")
        else:
            loc_dict = {l.nombre: l.id for l in locs}
            
            # 1. Selector de Ubicaciones
            st.markdown("##### 1. Configurar Ruta")
            
            # 🛡️ VECTOR 2: Bloqueo del "Carrito Mutante"
            # Si hay ítems en el carrito, bloqueamos el origen y destino para evitar errores humanos
            cart_has_items = len(st.session_state.transfer_cart) > 0
            
            c1, c2 = st.columns(2)
            sel_src = c1.selectbox("Ubicación Origen (De donde sale) 📤", list(loc_dict.keys()), disabled=cart_has_items)
            sel_dest = c2.selectbox("Ubicación Destino (A donde llega) 📥", list(loc_dict.keys()), disabled=cart_has_items)
            
            if cart_has_items:
                st.caption("🔒 *El Origen y Destino están bloqueados porque ya tienes productos en el carrito. Para cambiarlos, elimina los productos de la tabla.*")
            
            src_id_val = loc_dict[sel_src]
            dest_id_val = loc_dict[sel_dest]

            st.markdown("---")
            
            # 2. Buscador Reactivo
            st.markdown("##### 2. ➕ Agregar Productos")
            prod_dict = {f"{p.sku} - {p.nombre}": (p.id, p.sku, p.nombre) for p in productos}
            
            c_prod, c_qty, c_btn = st.columns([3, 1, 1])
            sel_prod_key = c_prod.selectbox("Buscar por SKU o Nombre 🔍", list(prod_dict.keys()))
            qty_input = c_qty.number_input("Cantidad", min_value=1.0, step=1.0)
            
            if c_btn.button("Agregar ➕", width="stretch"):
                if sel_src == sel_dest:
                    st.error("Origen y destino no pueden ser iguales.")
                else:
                    prod_id, prod_sku, prod_nombre = prod_dict[sel_prod_key]
                    stock_disp = _get_stock_for_location(prod_id, src_id_val)
                    
                    qty_en_carrito = sum(item['qty'] for item in st.session_state.transfer_cart if item['product_id'] == prod_id)
                    qty_total_requerida = qty_en_carrito + qty_input
                    
                    if qty_total_requerida > stock_disp:
                        st.error(f"❌ Stock insuficiente. Tienes {stock_disp:.0f} en {sel_src}. Ya tienes {qty_en_carrito:.0f} en el carrito.")
                    else:
                        item_existente = next((item for item in st.session_state.transfer_cart if item['product_id'] == prod_id), None)
                        if item_existente:
                            item_existente['qty'] += qty_input
                        else:
                            st.session_state.transfer_cart.append({
                                'product_id': prod_id, 'sku': prod_sku, 'Producto': prod_nombre, 'qty': qty_input, 'Eliminar ❌': False
                            })
                        st.rerun()

            # 3. Visualización y Procesamiento
            if st.session_state.transfer_cart:
                st.markdown("---")
                st.markdown("##### 📝 Resumen del Lote")
                df_cart = pd.DataFrame(st.session_state.transfer_cart)
                
                # 🛡️ VECTOR 4: Prevención de inyecciones negativas o ceros en la UI
                edited_df = st.data_editor(
                    df_cart, hide_index=True, width='stretch', disabled=["product_id", "sku", "Producto"],
                    column_config={
                        "product_id": None, "sku": "SKU", "Producto": "Descripción",
                        "qty": st.column_config.NumberColumn("Cantidad", min_value=1.0, step=1.0, required=True, help="Debe ser mayor a 0"),
                        "Eliminar ❌": st.column_config.CheckboxColumn("Eliminar ❌", default=False)
                    }
                )
                
                if edited_df["Eliminar ❌"].any() or not edited_df["qty"].equals(df_cart["qty"]):
                    cart_actualizado = edited_df[~edited_df["Eliminar ❌"]].to_dict('records')
                    for item in cart_actualizado: item["Eliminar ❌"] = False
                    st.session_state.transfer_cart = cart_actualizado
                    st.rerun()

                if st.button("🚀 Procesar Traspaso de Lote", type="primary", width="stretch"):
                    with st.spinner("Enviando lote a Loyverse y BD..."):
                        éxito = _register_batch_movements(
                            cart_items=st.session_state.transfer_cart,
                            src_id=src_id_val, dest_id=dest_id_val, m_type="Traspaso", status="Completado"
                        )
                        if éxito:
                            st.session_state.transfer_cart = []
                            st.success("✅ ¡Traspaso múltiple procesado con éxito!")
                            time.sleep(1.5)
                            st.rerun()
    finally:
        db.close()