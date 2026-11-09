#!/usr/bin/env python
# coding: utf-8

"""
Módulo de Aliados Comerciales y Consignación (V7.4 - Arquitectura RDBMS)
Maneja inventario de doble entrada, liquidaciones y catálogos maestros.
"""
import streamlit as st
import pandas as pd
import uuid
import time
from sqlalchemy import func
from datetime import datetime
from sqlalchemy import or_, and_
from src.services.loyverse_api import LoyverseAPI
from src.services.inventory_logic import registrar_movimiento_kardex # 🛡️ EL MOTOR INMUTABLE
from src.database.db_core import SessionLocal
from sqlalchemy.exc import IntegrityError
# Asegúrate de incluir ProductStockLocation en tus importaciones de models
from src.database.models import (
    Product, InventoryLocation, InventoryMovement, 
    CommercialAlly, LedgerTransaction, AuditLog, ProductStockLocation
)
from src.logger_config import get_custom_logger

# Instanciar los dos loggers desde la fábrica centralizada
inv_logger = get_custom_logger("inventario", "modulo_inventario.log")
seguridad_logger = get_custom_logger("seguridad", "seguridad_auditoria.log")

# ==========================================
# CAPA DE DATOS: OPERACIONES B2B (CRUD)
# ==========================================

def _get_current_user():
    return st.session_state.get("username", "sistema")

def _load_inventory_status() -> pd.DataFrame:
    """Extrae el stock actual instantáneamente desde la vista materializada (CQRS)."""
    db = SessionLocal()
    try:
        # SELECT directo que trae TODO lo que la UI necesita sin usar Pandas para agrupar
        query = db.query(
            ProductStockLocation.location_id.label('loc_id'),
            InventoryLocation.nombre.label('nombre_loc'),
            InventoryLocation.tipo.label('tipo'),
            ProductStockLocation.product_id,
            Product.sku,
            Product.nombre,
            Product.costo_base,
            Product.precio_venta,
            ProductStockLocation.stock_actual
        ).join(
            Product, ProductStockLocation.product_id == Product.id
        ).join(
            InventoryLocation, ProductStockLocation.location_id == InventoryLocation.id
        ).filter(
            ProductStockLocation.stock_actual > 0  # Ignorar basura de stock en cero
        )

        df = pd.read_sql(query.statement, db.bind)
        return df if not df.empty else pd.DataFrame()
    except Exception as e:
        inv_logger.error(f"Fallo crítico al leer stock materializado: {e}", exc_info=True)
        st.error("Error al cargar el inventario.")
        return pd.DataFrame()
    finally:
        db.close()
    


def _get_or_create_safe_location(db_session, nombre_loc: str, tipo: str = "Interno"):
    """Obtiene una ubicación o la crea de forma atómica a prueba de concurrencia (Get-or-Create)."""
    loc = db_session.query(InventoryLocation).filter_by(nombre=nombre_loc).first()
    if loc:
        return loc

    try:
        # Usamos SAVEPOINT para que si falla por concurrencia, no aborte toda la transacción padre
        with db_session.begin_nested():
            nueva_loc = InventoryLocation(nombre=nombre_loc, tipo=tipo)
            db_session.add(nueva_loc)
        return nueva_loc
    except IntegrityError:
        # RACE CONDITION: Alguien más la creó en el mismo milisegundo. Consultamos de nuevo.
        return db_session.query(InventoryLocation).filter_by(nombre=nombre_loc).first()



def _process_liquidation(ally_id, product_id, location_id, qty_sold, qty_returned, unit_price, estado_devolucion="Buen Estado"):
    """Procesa liquidación blindando las matemáticas y atando la deuda B2B a finanzas."""
    db = SessionLocal()
    try:
        user = _get_current_user()
        
        # --- BARRERA INTACTA: Inyección Negativa ---
        if float(qty_sold) < 0 or float(qty_returned) < 0:
            raise ValueError("Las cantidades vendidas o devueltas no pueden ser negativas.")
        if float(qty_sold) == 0 and float(qty_returned) == 0:
            raise ValueError("Debe indicar al menos una unidad vendida o devuelta.")

        prod = db.query(Product).filter_by(id=product_id).first()
        loc = db.query(InventoryLocation).filter_by(id=location_id).first()
        nombre_prod = prod.nombre if prod else "Producto Desconocido"
        nombre_aliado = loc.nombre if loc else "Aliado Desconocido"

        # 🛡️ 1. FIREWALL MATEMÁTICO ANTI-CONCURRENCIA (Vector 2)
        stock_loc = db.query(ProductStockLocation).filter_by(product_id=product_id, location_id=location_id).with_for_update().first()
        if not stock_loc or stock_loc.stock_actual < (qty_sold + qty_returned):
            raise ValueError(f"Intento de sobre-liquidar detectado. Stock real disponible: {stock_loc.stock_actual if stock_loc else 0}")

        notas_comunes = f"Liquidación - Aliado: {nombre_aliado} | Prod: {nombre_prod}"

        # 1. Registrar Ventas
        if qty_sold > 0:
            registrar_movimiento_kardex(db, product_id, location_id, -qty_sold, "Venta_Consignacion", user)
            
            # 🛡️ 2. TRAZABILIDAD B2B ESTRICTA (Vector 1) - Atamos la deuda al location_id (que actuará como vendor/aliado temporario o enlazable)
            ledger_tx = LedgerTransaction(
                id=str(uuid.uuid4()), 
                fecha=datetime.now(), 
                tipo="Cuentas por Cobrar", 
                entidad_id=str(location_id), # <- BARRERA AÑADIDA: Vinculación Fuerte
                monto=(qty_sold * unit_price), 
                status="Pendiente", 
                notas=notas_comunes + " (Consignación)", 
                created_by=user
            )
            db.add(ledger_tx)

        # 2. Registrar Devoluciones (Lógica de Cuarentena Dinámica y Segura)
        if qty_returned > 0:
            if estado_devolucion == "Dañado/Merma":
                bodega_destino = _get_or_create_safe_location(db, "Bodega de Cuarentena")
            else:
                bodega_destino = db.query(InventoryLocation).filter(InventoryLocation.tipo == "Interno", InventoryLocation.nombre != "Bodega de Cuarentena").first()
                if not bodega_destino:
                    bodega_destino = _get_or_create_safe_location(db, "Almacén Principal")

            # 🛡️ EVENT SOURCING INTACTO
            registrar_movimiento_kardex(db, product_id, location_id, -qty_returned, "Devolucion", user)
            registrar_movimiento_kardex(db, product_id, bodega_destino.id, qty_returned, "Devolucion", user)

        db.commit()
        inv_logger.info(f"Liquidación Exitosa - Aliado: {location_id} | Producto: {product_id} | Vendidos: {qty_sold} | Devueltos: {qty_returned} (Estado: {estado_devolucion})")
        st.success(f"✅ Liquidación procesada exitosamente.")
        return True

    except ValueError as ve:
        db.rollback()
        inv_logger.warning(f"Liquidación rechazada por validación de negocio: {str(ve)}")
        st.error(f"❌ Transacción rechazada: {str(ve)}")
        return False
    except Exception as e:
        db.rollback()
        seguridad_logger.error(f"Error crítico en _process_liquidation: {str(e)}", exc_info=True)
        st.error("❌ Error de base de datos. Operación cancelada.")
        return False
    finally:
        db.close()


# ==========================================
# INTERFAZ DE USUARIO (VISTA STREAMLIT)
# ==========================================

def render_allies_view():
    st.markdown("### 🤝 Aliados Comerciales")
    
    # Carga de datos base estrictamente protegida contra fugas de conexión
    db = SessionLocal()
    try:
        productos = db.query(Product).all()
        ubicaciones = db.query(InventoryLocation).all()
    except Exception as e:
        st.error(f"Error crítico conectando a la base de datos: {str(e)}")
        return  # Detener el renderizado si no hay datos
    finally:
        db.close()

    opciones_aliados = [
        "📊 Dashboard de Aliados", 
        "✂️ Cortes y Liquidaciones"
    ]
    vista_seleccionada = st.pills("Sub-Navegación Aliados", options=opciones_aliados, default=opciones_aliados[0], label_visibility="collapsed")
    st.divider()

    # -----------------------------------------------------------------
    # TAB 1: DASHBOARD
    # -----------------------------------------------------------------
    if vista_seleccionada == "📊 Dashboard de Aliados":
        st.info("Visualiza el inventario físico en tus Almacenes Internos y la mercancía en Consignación (Aliados).")
        df_stock = _load_inventory_status()
        
        if not df_stock.empty:
            df_stock['valor_mercancia'] = df_stock['stock_actual'] * df_stock['costo_base']
            
            df_internos = df_stock[df_stock['tipo'] == 'Interno'].copy()
            df_aliados = df_stock[df_stock['tipo'] == 'Aliado'].copy()
            
            col_int, col_ext = st.columns(2)
            
            with col_int:
                st.markdown("#### 🏢 Almacenes Internos (Bodegas)")
                if not df_internos.empty:
                    st.metric("Total Items en Bodega", f"{df_internos['stock_actual'].sum():,.0f} und")
                    st.dataframe(
                        df_internos[['nombre_loc', 'nombre', 'stock_actual', 'valor_mercancia']], 
                        width='stretch', hide_index=True,
                        column_config={
                            "nombre_loc": "Bodega", "nombre": "Producto",
                            "stock_actual": "Stock", "valor_mercancia": st.column_config.NumberColumn("Valor (USD)", format="$%.2f")
                        }
                    )
                else:
                    st.caption("No hay mercancía en bodegas internas.")

            with col_ext:
                st.markdown("#### 🤝 Aliados (Consignación)")
                if not df_aliados.empty:
                    st.metric("Total Items en Consignación", f"{df_aliados['stock_actual'].sum():,.0f} und")
                    st.dataframe(
                        df_aliados[['nombre_loc', 'nombre', 'stock_actual', 'valor_mercancia']], 
                        width='stretch', hide_index=True,
                        column_config={
                            "nombre_loc": "Aliado", "nombre": "Producto",
                            "stock_actual": "Stock", "valor_mercancia": st.column_config.NumberColumn("Valor (USD)", format="$%.2f")
                        }
                    )
                else:
                    st.caption("No hay mercancía asignada a Aliados en este momento.")
        else:
            st.warning("No hay datos de inventario registrados actualmente.")


    # -----------------------------------------------------------------
    # TAB 2: CORTES Y LIQUIDACIONES
    # -----------------------------------------------------------------
    elif vista_seleccionada == "✂️ Cortes y Liquidaciones":
        st.markdown("#### Procesar Corte de Consignación")
        
        ubicaciones_aliados = [l for l in ubicaciones if l.tipo == "Aliado"]
        if not ubicaciones_aliados:
            st.warning("No hay ubicaciones de tipo 'Aliado' creadas en el sistema. Ve a 'Catálogos Maestros'.")
        else:
            loc_corte = st.selectbox("Seleccionar Aliado (Ubicación)", [(l.id, l.nombre) for l in ubicaciones_aliados], format_func=lambda x: x[1])
            
            df_stock = _load_inventory_status()
            if loc_corte and not df_stock.empty:
                loc_id_val = loc_corte[0]
                df_loc = df_stock[df_stock['loc_id'] == loc_id_val]
                
                if df_loc.empty:
                    st.info("Este aliado no tiene mercancía asignada actualmente.")
                else:
                    st.write(f"**Inventario actual en poder de: {loc_corte[1]}**")
                    
                    with st.form("liq_form"):
                        prod_liq = st.selectbox("Producto a Liquidar", [(row['product_id'], row['nombre'], row['stock_actual'], row['precio_venta']) for idx, row in df_loc.iterrows()], format_func=lambda x: f"{x[1]} (Stock: {x[2]:.0f})")
                        
                        st.caption("Indique cuántas unidades se vendieron y/o cuántas le devolvieron físicamente.")
                        c_v, c_d = st.columns(2)
                        
                        p_id = prod_liq[0]
                        max_qty = int(prod_liq[2])
                        p_price = float(prod_liq[3])
                        
                        v_qty = c_v.number_input("Vendidos (A Facturar)", min_value=0, max_value=max_qty, value=0)
                        d_qty = c_d.number_input("Devueltos (Retornan a Almacén)", min_value=0, max_value=max_qty, value=0)
                        
                        # --- NUEVA UI: SELECTOR DE CUARENTENA ---
                        estado_dev = st.selectbox("Estado de la Devolución (Si aplica)", ["Buen Estado", "Dañado/Merma"])
                        
                        submit_liq = st.form_submit_button("Generar Corte Definitivo")
                        
                        if submit_liq:
                            if (v_qty + d_qty) > max_qty:
                                st.error("Error: La suma excede el Stock Actual.")
                            elif (v_qty + d_qty) == 0:
                                st.warning("Debe registrar al menos 1 unidad.")
                            else:
                                # Le pasamos el nuevo parámetro 'estado_dev' al backend
                                _process_liquidation(ally_id=loc_id_val, product_id=p_id, location_id=loc_id_val, qty_sold=v_qty, qty_returned=d_qty, unit_price=p_price, estado_devolucion=estado_dev)
                                st.rerun()

