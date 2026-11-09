#!/usr/bin/env python
# coding: utf-8
import logging
seguridad_logger = logging.getLogger("erp_security_auditor")
import streamlit as st
import pandas as pd
import uuid
import time
import os
from pathlib import Path
from datetime import datetime, timedelta, date
from sqlalchemy.orm import joinedload
from sqlalchemy import func, cast, Date
from fpdf import FPDF
from werkzeug.utils import secure_filename
from functools import wraps
import re

# Configuración de carpetas para los comprobantes de pago
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DOCS_DIR = BASE_DIR / "data" / "docs" / "finance"
DOCS_DIR.mkdir(parents=True, exist_ok=True)


# Importaciones de la arquitectura base
from sqlalchemy.orm.exc import StaleDataError
from src.services.inventory_logic import registrar_movimiento_kardex
from src.database.db_core import SessionLocal
from src.database.models import (
    Product, Vendor, PurchaseOrder,
    PurchaseOrderItem, InventoryMovement, InventoryLocation,
    LedgerTransaction
)














def requiere_admin_backend(func):
    """
    Decorador de Seguridad DevSecOps.
    Bloquea la ejecución a nivel backend si el usuario no tiene privilegios.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        # Leemos el estado de sesión directamente. Ajusta "rol" o "username" si en tu sistema se llaman distinto.
        usuario_actual = st.session_state.get("username", "invitado")
        rol_actual = st.session_state.get("rol", "usuario") 

        # Validación estricta
        if rol_actual not in ["Administrador", "Admin"] and usuario_actual != "acroscarlos":
            st.error("⛔ Acceso Denegado: Excepción de seguridad. No tienes privilegios para transacciones financieras.")
            
            # Registro Crítico de Auditoría
            if 'seguridad_logger' in globals():
                seguridad_logger.critical(f"🛡️ BYPASS BLOQUEADO | Usuario: {usuario_actual} | Intento de ejecutar: {func.__name__}")
            
            return False # Aborta la operación silenciosamente en el backend
            
        return func(*args, **kwargs)
    return wrapper

def _generar_pdf_orden(po_dict: dict, logo_path: str = None) -> bytes:
    """Genera un PDF corporativo de Orden de Compra basado en la plantilla UNI-T usando FPDF."""
    pdf = FPDF()
    pdf.add_page()
    
    # Colores Corporativos (Azul Oscuro UNI-T)
    azul_oscuro = (0, 51, 102)
    
    # ==========================================
    # 1. CABECERA CORPORATIVA Y CAJA DE PO
    # ==========================================
    # Insertar Logo si existe
    if logo_path:
        try:
            pdf.image(logo_path, x=10, y=10, w=25)
        except:
            pass # Si falla el logo, simplemente no lo pone
            
    # Datos de UNI-T (Izquierda)
    pdf.set_xy(40, 10)
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(100, 5, "UNI-T VENEZUELA, C.A.", ln=True)
    pdf.set_font("Arial", '', 9)
    pdf.set_x(40)
    pdf.cell(100, 4, "C.C. Galerias Avila, nivel Feria, Local F67", ln=True)
    pdf.set_x(40)
    pdf.cell(100, 4, "Caracas, 1010, Venezuela.", ln=True)
    pdf.set_x(40)
    pdf.set_font("Arial", 'B', 9)
    pdf.cell(12, 4, "R.I.F.:", ln=False)
    pdf.set_font("Arial", '', 9)
    pdf.cell(88, 4, "J-50174299-5", ln=True)
    pdf.set_x(40)
    pdf.set_font("Arial", 'B', 9)
    pdf.cell(12, 4, "Web:", ln=False)
    pdf.set_font("Arial", '', 9)
    pdf.cell(88, 4, "www.mitiendaunit.com", ln=True)
    
    # Cuadro de Identificación de la Orden (Derecha)
    pdf.set_xy(130, 10)
    pdf.set_fill_color(*azul_oscuro)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Arial", 'B', 11)
    pdf.cell(70, 7, "ORDEN DE COMPRA", border=1, ln=True, align='C', fill=True)
    
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Arial", 'B', 9)
    
    # Campo: Numero
    pdf.set_x(130)
    pdf.cell(22, 6, " N de Orden:", border='L', ln=False)
    pdf.set_font("Arial", '', 9)
    pdf.cell(48, 6, str(po_dict.get('id', 'N/A')), border='R', ln=True)
    
    # Campo: Fecha
    pdf.set_x(130)
    pdf.set_font("Arial", 'B', 9)
    pdf.cell(22, 6, " Fecha:", border='L', ln=False)
    pdf.set_font("Arial", '', 9)
    fecha_po = po_dict.get('fecha')
    if not isinstance(fecha_po, str):
        fecha_po = datetime.now().strftime("%d/%m/%Y")
    pdf.cell(48, 6, fecha_po, border='R', ln=True)
    
    # Campo: Comprador
    pdf.set_x(130)
    pdf.set_font("Arial", 'B', 9)
    pdf.cell(22, 6, " Comprador:", border='L', ln=False)
    pdf.set_font("Arial", '', 9)
    pdf.cell(48, 6, "Carlos Carreno", border='R', ln=True)
    
    # Campo: Teléfono
    pdf.set_x(130)
    pdf.set_font("Arial", 'B', 9)
    pdf.cell(22, 6, " Telefono:", border='LB', ln=False)
    pdf.set_font("Arial", '', 9)
    pdf.cell(48, 6, "+58 414 1510661", border='RB', ln=True)
    
    # ==========================================
    # 2. DATOS DEL PROVEEDOR
    # ==========================================
    pdf.set_xy(10, 45)
    pdf.set_font("Arial", 'B', 9)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(190, 6, "  DATOS DEL PROVEEDOR", border=1, ln=True, fill=True)
    
    pdf.set_font("Arial", 'B', 9)
    pdf.cell(30, 6, " RAZON SOCIAL:", border='L', ln=False)
    pdf.set_font("Arial", '', 9)
    vendor_name = str(po_dict.get('vendor_nombre', '')).encode('latin-1', 'ignore').decode('latin-1')[:80]
    pdf.cell(160, 6, vendor_name, border='R', ln=True)
    
    pdf.set_font("Arial", 'B', 9)
    pdf.cell(30, 6, " RIF/TAX ID:", border='LB', ln=False)
    pdf.set_font("Arial", '', 9)
    pdf.cell(160, 6, str(po_dict.get('vendor_rif', 'No Registrado')), border='RB', ln=True)
    
    # ==========================================
    # 3. TABLA DE ÍTEMS
    # ==========================================
    pdf.ln(8)
    pdf.set_fill_color(*azul_oscuro)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Arial", 'B', 9)
    
    pdf.cell(30, 7, "SKU", border=1, align='C', fill=True)
    pdf.cell(80, 7, "DESCRIPCION", border=1, align='C', fill=True)
    pdf.cell(20, 7, "CANTIDAD", border=1, align='C', fill=True)
    pdf.cell(30, 7, "COSTO UNIT.", border=1, align='C', fill=True)
    pdf.cell(30, 7, "TOTAL LINEA", border=1, align='C', fill=True)
    pdf.ln()
    
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Arial", '', 9)
    
    gran_total = 0.0
    for item in po_dict.get('items', []):
        sku = str(item.get('sku', 'N/A')).encode('latin-1', 'ignore').decode('latin-1')[:15]
        nombre_item = str(item.get('producto_nombre', 'Item')).encode('latin-1', 'ignore').decode('latin-1')[:42]
        qty = float(item.get('cantidad', 0))
        precio_unit = float(item.get('costo_unitario', 0))
        total_linea = qty * precio_unit
        gran_total += total_linea
        
        pdf.cell(30, 6, sku, border=1, align='C')
        pdf.cell(80, 6, nombre_item, border=1)
        pdf.cell(20, 6, str(qty), border=1, align='C')
        pdf.cell(30, 6, f"${precio_unit:.2f}", border=1, align='C')
        pdf.cell(30, 6, f"${total_linea:.2f}", border=1, align='C')
        pdf.ln()
        
    # ==========================================
    # 4. TOTALES
    # ==========================================
    pdf.ln(3)
    envio = float(po_dict.get('costos_adicionales_envio') or 0.0)
    total_pagar = gran_total + envio
    
    pdf.set_x(120)
    pdf.set_font("Arial", 'B', 9)
    pdf.cell(40, 6, "Subtotal:", border=1, align='R')
    pdf.set_font("Arial", '', 9)
    pdf.cell(40, 6, f"${gran_total:.2f}", border=1, align='C')
    pdf.ln()
    
    if envio > 0:
        pdf.set_x(120)
        pdf.set_font("Arial", 'B', 9)
        pdf.cell(40, 6, "Envio/Adicionales:", border=1, align='R')
        pdf.set_font("Arial", '', 9)
        pdf.cell(40, 6, f"${envio:.2f}", border=1, align='C')
        pdf.ln()
    
    pdf.set_x(120)
    pdf.set_font("Arial", 'B', 10)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(40, 8, "TOTAL DE LA ORDEN:", border=1, align='R', fill=True)
    pdf.cell(40, 8, f"${total_pagar:.2f}", border=1, align='C', fill=True)
    pdf.ln()
    
    # ==========================================
    # 5. PIE DE PÁGINA (Términos legales)
    # ==========================================
    # Posicionamos el texto al fondo de la página
    pdf.set_y(245)
    pdf.set_font("Arial", 'B', 8)
    pdf.cell(0, 4, "CONDICIONES DE LA ORDEN DE COMPRA:", ln=True)
    pdf.set_font("Arial", '', 8)
    pdf.cell(0, 4, "1. Por favor confirmar la recepcion de esta Orden de Compra y la fecha estimada de despacho.", ln=True)
    pdf.cell(0, 4, "2. Facturar a nombre de UNI-T VENEZUELA, C.A. (RIF: J-50174299-5).", ln=True)
    pdf.cell(0, 4, "3. Todos los envios deben incluir una copia de este documento o referenciar el N de PO en la guia de carga.", ln=True)
    
    return pdf.output(dest='S').encode('latin-1')






def _generate_po_id(vendor_nombre: str) -> str:
    """Genera un ID corporativo: PO-[PRO]-[DDMMYY]-[NNN]"""
    db = SessionLocal()
    try:
        # 1. Extraer las primeras 3 letras del proveedor (solo letras, sin espacios/símbolos)
        nombre_limpio = re.sub(r'[^A-Za-z]', '', str(vendor_nombre or 'XXX'))
        prefijo_prov = nombre_limpio.ljust(3, 'X')[:3].upper()
        
        # 2. Fecha actual en formato DDMMYY
        hoy = date.today()
        fecha_str = hoy.strftime('%d%m%y')
        
        # 3. Contar cuántas órdenes existen HOY para este formato y generar el correlativo
        prefijo_base = f"PO-{prefijo_prov}-{fecha_str}"
        
        # Contamos cuántas órdenes empiezan con este prefijo base hoy
        from src.database.models import PurchaseOrder # Asegúrate de tenerlo importado arriba
        cantidad_hoy = db.query(PurchaseOrder).filter(
            PurchaseOrder.id.like(f"{prefijo_base}-%")
        ).count()
        
        correlativo = str(cantidad_hoy + 1).zfill(3) # Rellena con ceros: 001, 002...
        
        return f"{prefijo_base}-{correlativo}"
    except Exception as e:
        # Fallback seguro en caso de error de base de datos
        fecha_fallback = datetime.now().strftime('%d%m%y')
        hash_fallback = str(uuid.uuid4()).split('-')[0][:3].upper()
        return f"PO-ERR-{fecha_fallback}-{hash_fallback}"
    finally:
        db.close()



def _get_all_real_stock(db) -> dict:
    """Calcula el stock real global para todos los productos en una sola consulta vectorizada."""
    entradas = db.query(
        InventoryMovement.product_id,
        func.sum(InventoryMovement.cantidad).label('total_in')
    ).filter(
        InventoryMovement.dest_location_id != None,
        InventoryMovement.status == "Completado"
    ).group_by(InventoryMovement.product_id).all()

    salidas = db.query(
        InventoryMovement.product_id,
        func.sum(InventoryMovement.cantidad).label('total_out')
    ).filter(
        InventoryMovement.source_location_id != None,
        InventoryMovement.status == "Completado"
    ).group_by(InventoryMovement.product_id).all()

    stock_dict = {}
    for row in entradas:
        stock_dict[row.product_id] = row.total_in

    for row in salidas:
        stock_dict[row.product_id] = stock_dict.get(row.product_id, 0.0) - row.total_out

    return stock_dict



# ==========================================
# FUNCIÓN MATEMÁTICA (MAC)
# ==========================================
def _calcular_nuevo_costo_promedio(
    stock_actual: float, 
    costo_actual: float, 
    cantidad_recibida: float, 
    costo_nuevo: float, 
    costos_adicionales_prorrateados: float = 0.0
) -> float:
    """Calcula el Costo Promedio Ponderado (MAC) matemáticamente blindado."""
    
    # 1. BLOQUEO: Precio Nulo/Cero
    if costo_nuevo <= 0 and cantidad_recibida > 0:
        raise ValueError("Corrupción MAC: No se puede recepcionar inventario con costo unitario cero o negativo.")

    # 2. BLOQUEO: Recepciones Negativas
    if cantidad_recibida < 0:
        raise ValueError("Corrupción MAC: La cantidad recibida no puede ser negativa.")

    # 3. MITIGACIÓN 'CISNE NEGRO': Formateo de Stock Negativo
    # Si hay stock negativo por descuadres previos, el valor financiero base parte de 0 
    # para no "subsidiar" ni corromper el nuevo lote entrante.
    stock_financiero_valido = max(0.0, stock_actual)

    stock_total = stock_financiero_valido + cantidad_recibida
    
    # Prevención de División por Cero
    if stock_total <= 0:
        return round(costo_nuevo if cantidad_recibida > 0 else costo_actual, 2)

    # Cálculo Ponderado Estricto
    valor_inventario_actual = stock_financiero_valido * costo_actual
    valor_mercancia_entrante = (cantidad_recibida * costo_nuevo) + costos_adicionales_prorrateados
    
    nuevo_mac = (valor_inventario_actual + valor_mercancia_entrante) / stock_total
    
    # 4. PRECISIÓN FINANCIERA: Redondeo exacto a 2 decimales
    return round(nuevo_mac, 2)

# ==========================================
# CAPA DE DATOS: LECTURA
# ==========================================

def _get_current_user() -> str:
    return st.session_state.get("username", "sistema")



def _get_real_stock(db, product_id: str) -> float:
    """Calcula el stock real global sumando todos los almacenes."""
    entradas = db.query(func.sum(InventoryMovement.cantidad)).filter(
        InventoryMovement.product_id == product_id,
        InventoryMovement.dest_location_id != None
    ).scalar() or 0.0
    
    salidas = db.query(func.sum(InventoryMovement.cantidad)).filter(
        InventoryMovement.product_id == product_id,
        InventoryMovement.source_location_id != None
    ).scalar() or 0.0
    
    return float(entradas - salidas)


def _load_suppliers() -> list:
    """Extrae y retorna diccionarios simples para evitar DetachedInstanceError."""
    db = SessionLocal()
    try:
        suppliers = db.query(Vendor).all()
        return [{"id": s.id, "nombre": s.nombre} for s in suppliers]
    finally:
        db.close()

def _load_products_with_stock() -> pd.DataFrame:
    """Carga los productos y su stock real sumando todas las ubicaciones dinámicas."""
    db = SessionLocal()
    try:
        # 🛡️ Cargamos el producto y todas sus ubicaciones de stock en 1 sola consulta (Eager Loading)
        productos = db.query(Product).options(joinedload(Product.stocks)).all()
        
        if not productos:
            return pd.DataFrame(columns=["id", "Producto", "En Stock", "Entrantes", "Costo Actual"])

        data = []
        for p in productos:
            # 🛡️ Suma dinámica: No importa si tienes 2 o 50 tiendas, esto suma el stock total
            stock_real = sum(stock.stock_actual for stock in p.stocks)

            data.append({
                "id": p.id,
                "Producto": p.nombre,
                "En Stock": stock_real,
                "Entrantes": p.inventario_entrante or 0.0,
                "Costo Actual": p.costo_base or 0.0
            })
        return pd.DataFrame(data)
    finally:
        db.close()

def _load_pos() -> pd.DataFrame:
    db = SessionLocal()
    try:
        # joinedload(PurchaseOrder.items) es vital para poder calcular el porcentaje recibido
        pos = db.query(PurchaseOrder).options(
            joinedload(PurchaseOrder.vendor), 
            joinedload(PurchaseOrder.items)
        ).order_by(PurchaseOrder.fecha_creacion.desc()).all()
        
        data = []
        for po in pos:
            # Calcular % Recibido
            total_pedido = sum(i.cantidad for i in po.items)
            total_recibido = sum(i.cantidad_recibida for i in po.items)
            pct_recibido = (total_recibido / total_pedido * 100) if total_pedido > 0 else 0.0

            data.append({
                "ID": po.id,  # <- Ojo aquí, es con punto
                "Fecha": po.fecha_creacion.strftime("%Y-%m-%d"),
                "Proveedor": po.vendor.nombre if po.vendor else "N/A",
                "Estado": po.status, # <- Ojo aquí, es con punto
                "Recibido": f"{pct_recibido:.0f}%",
                "Esperado para": po.fecha_esperada.strftime("%Y-%m-%d") if po.fecha_esperada else "-",
                "Total": po.total_orden # <- Ojo aquí, es con punto
            })
        return pd.DataFrame(data)
    finally:
        db.close()

# ==========================================
# CAPA DE DATOS: TRANSACCIONES (ACID)
# ==========================================

def _save_po(vendor_id: str, fecha_esperada, notas: str, costos_envio: float, df_items: pd.DataFrame, status: str):
    """Guarda la Orden de Compra (Borrador o Esperado) de forma atómica."""
    db = SessionLocal()
    try:
        # 🛡️ CORRECCIÓN: Buscamos el proveedor en la base de datos usando su ID
        from src.database.models import Vendor # Por si no lo tienes importado arriba
        proveedor = db.query(Vendor).filter_by(id=vendor_id).first()
        nombre_proveedor = proveedor.nombre if proveedor else "GEN" # 'GEN' de Genérico si falla
        # Ahora sí, generamos el ID con el nombre real
        po_id = _generate_po_id(nombre_proveedor)
        total_items = 0.0
        po = PurchaseOrder(
            id=po_id,
            vendor_id=vendor_id,
            status=status,
            fecha_esperada=fecha_esperada,
            notas=notas,
            costos_adicionales_envio=costos_envio,
            total_orden=0.0
        )
        db.add(po)

        for _, row in df_items.iterrows():
            prod_id = row['id']
            qty = float(row['Cantidad a Pedir'])
            cost = float(row['Costo Unitario'])

            if qty <= 0:
                continue

            line_total = qty * cost
            total_items += line_total

            item = PurchaseOrderItem(
                id=str(uuid.uuid4()),
                po_id=po_id,
                product_id=prod_id,
                cantidad=qty,
                costo_unitario=cost,
                total_linea=line_total
            )
            db.add(item)

            # Si se marca como "Esperado", sumar al inventario entrante del catálogo
            if status == "Esperado":
                prod = db.query(Product).filter_by(id=prod_id).first()
                if prod:
                    prod.inventario_entrante = (prod.inventario_entrante or 0.0) + qty

        po.total_orden = total_items + costos_envio
        db.commit()
        _export_transit_csv()
        st.success(f"✅ Orden de Compra creada exitosamente (Status: {status}).")
        return True
    except Exception as e:
        db.rollback()
        st.error(f"❌ Error crítico guardando la orden: {e}")
        return False
    finally:
        db.close()

def _load_po_details(po_id: str) -> dict:
    """Serializa la Orden de Compra antes de cerrar la conexión de SQLAlchemy."""
    db = SessionLocal()
    try:
        po = db.query(PurchaseOrder).options(
            joinedload(PurchaseOrder.vendor),
            joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.product)
        ).filter(PurchaseOrder.id == po_id).first()

        if not po:
            return None

        # Convertimos a diccionario seguro. 
        # (Aquí la BD está abierta, 'po' es un objeto, usamos PUNTO para extraer la data)
        return {
            "id": po.id, 
            "status": po.status, 
            "notas": po.notas,
            "costos_adicionales_envio": po.costos_adicionales_envio, 
            "total_orden": po.total_orden,
            "vendor_nombre": po.vendor.nombre if po.vendor else "N/A",
            "items": [
                {
                    "id": item.id, 
                    "product_id": item.product_id, 
                    "producto_nombre": item.product.nombre if item.product else "N/A",
                    "sku": item.product.sku if item.product else "N/A", # 🛡️ CORRECCIÓN: Inyectamos el SKU para el PDF
                    "cantidad": item.cantidad, 
                    "cantidad_recibida": item.cantidad_recibida, 
                    "costo_unitario": item.costo_unitario
                } for item in po.items
            ]
        }
    finally:
        db.close()

@requiere_admin_backend
def _mark_as_paid(po_id: str, fecha_pago, referencia: str, uploaded_file, tipo_pago: str, dias_credito: int):
    """Registra el pago en finanzas, guarda comprobantes en estructura dinámica. (MAC movido a recepción)"""
    db = SessionLocal()
    try:
        # 🛡️ BARRERA AÑADIDA: .with_for_update() para bloquear concurrencia
        po = db.query(PurchaseOrder).options(joinedload(PurchaseOrder.vendor), joinedload(PurchaseOrder.items)).filter_by(id=po_id).with_for_update().first()
        if not po or po.status != "Esperado": return False

        user = _get_current_user()
        total_items_value = sum(item.total_linea for item in po.items)

        # 1. Lógica de Finanzas y Crédito (INTACTA)
        status_ledger = "Pendiente" if tipo_pago == "Crédito" else "Pagado"
        vencimiento_real = fecha_pago + timedelta(days=dias_credito) if tipo_pago == "Crédito" else fecha_pago
        
        tx_id = str(uuid.uuid4())
        tx = LedgerTransaction(
            id=tx_id, fecha=fecha_pago, tipo="Cuentas por Pagar",
            entidad_id=po.vendor_id, monto=po.total_orden,
            vencimiento=vencimiento_real, notas=f"Pago de Orden {po.id} - {tipo_pago}. Ref: {referencia}",
            status=status_ledger, created_by=user
        )
        db.add(tx)

        # 2. Guardado Físico del Comprobante (INTACTA)
        if uploaded_file:
            meses = {1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
                     7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"}

            año_str = str(fecha_pago.year)
            mes_es = meses.get(fecha_pago.month, "Desconocido")
            vendor_nombre = po.vendor.nombre if po.vendor else "Proveedor_Desconocido"
            vendor_limpio = "".join([c if c.isalnum() else "_" for c in vendor_nombre])
            carpeta_tipo = "Credito" if tipo_pago == "Crédito" else "Recibos_Pago"

            target_dir = DOCS_DIR / año_str / mes_es / vendor_limpio / carpeta_tipo
            target_dir.mkdir(parents=True, exist_ok=True)

            sufijo = "Credito" if tipo_pago == "Crédito" else "Recibo"
            safe_name = secure_filename(f"{po_id}_{sufijo}.pdf")
            ext = uploaded_file.name.split('.')[-1] if '.' in uploaded_file.name else 'pdf'
            if ext.lower() not in ['pdf']: safe_name = safe_name.replace(".pdf", f".{ext}")

            with open(target_dir / safe_name, "wb") as f:
                f.write(uploaded_file.getbuffer())

        # (El paso 3 de Recalcular MAC se eliminó de aquí)

        po.status = "Pagado"
        db.commit()
        _export_transit_csv()
        st.success(f"💸 Orden procesada ({tipo_pago}). Registrado en Finanzas.")
        return True
    except StaleDataError:
        db.rollback()
        msg = "Colisión detectada: Otro usuario acaba de actualizar el costo de uno de estos productos. Reintente la operación."
        seguridad_logger.warning(msg)
        st.error(f"⚠️ {msg}")
        return False
    except Exception as e:
        db.rollback()
        seguridad_logger.error(f"Error procesando pago PO {po_id}: {str(e)}", exc_info=True)
        st.error(f"❌ Error registrando pago: {e}")
        return False
    finally:
        db.close()



def _process_reception(po_id: str, items_to_receive: dict):
    """Procesa la entrada física validando matemáticamente para proteger el MAC."""
    db = SessionLocal()
    try:
        # 🛡️ BARRERA AÑADIDA: .with_for_update()
        po = db.query(PurchaseOrder).options(joinedload(PurchaseOrder.items)).filter_by(id=po_id).with_for_update().first()
        
        # --- BARRERA INTACTA: Máquina de estados ---
        if not po or po.status in ["Borrador", "Completado", "Cancelado"]: 
            raise ValueError(f"No se puede recibir mercancía de una orden en estado '{po.status if po else 'Desconocido'}'.")

        # Necesitamos el total de la orden para el prorrateo del MAC
        total_items_value = sum(item.total_linea for item in po.items)

        # --- BARRERA INTACTA: Validación estricta anti-inyección negativa ---
        for item_id, qty_receiving_now in items_to_receive.items():
            if float(qty_receiving_now) <= 0:
                raise ValueError(f"Violación de integridad: La cantidad a recibir debe ser mayor a 0.")

        bodega_principal = db.query(InventoryLocation).filter(InventoryLocation.tipo == "Interno").first()
        if not bodega_principal:
            bodega_principal = InventoryLocation(nombre="Almacén Principal", tipo="Interno")
            db.add(bodega_principal)
            db.flush()

        all_fully_received = True

        for item in po.items:
            qty_receiving_now = items_to_receive.get(item.id, 0.0)
            
            if qty_receiving_now > 0:
                # 🛡️ NUEVA BARRERA: Evitar Over-Receiving
                if item.cantidad_recibida + qty_receiving_now > item.cantidad:
                    raise ValueError(f"OVER-RECEIVING: Intentas recibir {qty_receiving_now} extra del SKU, pero solo faltan {item.cantidad - item.cantidad_recibida}.")

                # 🛡️ BARRERA AÑADIDA: .with_for_update() en el producto
                prod = db.query(Product).filter_by(id=item.product_id).with_for_update().first()
                if not prod: continue

                # 🛡️ NUEVA LÓGICA: Cálculo del MAC trasladado desde el pago
                prorrateo_envio = 0.0
                if total_items_value > 0:
                    valor_recibido = qty_receiving_now * item.costo_unitario
                    prorrateo_envio = (valor_recibido / total_items_value) * (po.costos_adicionales_envio or 0.0)

                stock_actual = _get_real_stock(db, prod.id)
                prod.costo_base = _calcular_nuevo_costo_promedio(
                    stock_actual, prod.costo_base or 0.0, qty_receiving_now, item.costo_unitario, prorrateo_envio
                )

                # --- TU LÓGICA INTACTA: Ajuste de cantidades y Kardex ---
                prod.inventario_entrante = max(0.0, (prod.inventario_entrante or 0.0) - qty_receiving_now)
                item.cantidad_recibida += qty_receiving_now

                registrar_movimiento_kardex(
                    db_session=db,
                    product_id=prod.id,
                    location_id=bodega_principal.id,
                    delta_qty=qty_receiving_now, 
                    m_type="Pedido_Fabrica",
                    user=_get_current_user()
                )

            if item.cantidad_recibida < item.cantidad:
                all_fully_received = False

        po.status = "Completado" if all_fully_received else "Recibido Parcialmente"
        db.commit()
        st.success("📦 Recepción física procesada exitosamente. Costo MAC actualizado.")
        return True

    except ValueError as ve:
        db.rollback()
        st.error(f"❌ Transacción rechazada: {str(ve)}")
        return False
    except Exception as e:
        db.rollback()
        seguridad_logger.error(f"Error crítico en _process_reception PO {po_id}: {str(e)}", exc_info=True)
        st.error("❌ Error de base de datos. Operación cancelada de forma segura.")
        return False
    finally:
        db.close()
        
def _cancel_po(po_id: str):
    """Cancela la orden y revierte el inventario entrante pendiente."""
    db = SessionLocal()
    try:
        # 🛡️ BARRERA AÑADIDA: .with_for_update()
        po = db.query(PurchaseOrder).options(joinedload(PurchaseOrder.items)).filter_by(id=po_id).with_for_update().first()
        if not po: return False

        # --- BARRERA INTACTA: Máquina de estados ---
        if po.status == "Completado":
            raise ValueError("No se puede cancelar una orden que ya ha sido recibida físicamente en bodega.")
        if po.status == "Cancelado":
            return True # Idempotencia

        # Restar lo que quedó pendiente del inventario entrante (TU LÓGICA INTACTA)
        if po.status in ["Esperado", "Recibido Parcialmente"]:
            for item in po.items:
                pendiente = item.cantidad - item.cantidad_recibida
                if pendiente > 0:
                    # 🛡️ BARRERA AÑADIDA: .with_for_update() en el producto
                    prod = db.query(Product).filter_by(id=item.product_id).with_for_update().first()
                    if prod: prod.inventario_entrante = max(0.0, (prod.inventario_entrante or 0.0) - pendiente)

        po.status = "Cancelado"
        db.commit()
        st.success("🚫 Orden cancelada. Inventario entrante ajustado.")
        return True

    except ValueError as ve:
        db.rollback()
        st.error(f"❌ Transacción rechazada: {str(ve)}")
        return False
    except Exception as e:
        db.rollback()
        seguridad_logger.error(f"Error crítico en _cancel_po PO {po_id}: {str(e)}", exc_info=True)
        st.error("❌ Error de base de datos. Operación cancelada de forma segura.")
        return False
    finally:
        db.close()
# ==========================================
# VISTA STREAMLIT (INTERFAZ DE USUARIO)
# ==========================================
def _export_transit_csv():
    """Exporta un CSV con la mercancía en tránsito (Esperado o Pagado)."""
    db = SessionLocal()
    try:
        # Buscar órdenes que están en camino
        pos = db.query(PurchaseOrder).options(joinedload(PurchaseOrder.items).joinedload(PurchaseOrderItem.product)).filter(
            PurchaseOrder.status.in_(["Esperado", "Pagado", "Recibido Parcialmente"])
        ).all()
        
        data = []
        for po in pos:
            for item in po.items:
                pendiente = item.cantidad - item.cantidad_recibida
                if pendiente > 0 and item.product:
                    data.append({
                        "REF": item.product.sku,
                        "Nombre del articulo": item.product.nombre,
                        "Nombre de la variante": "",
                        "Codigo de barras": "",
                        "Cantidad": pendiente,
                        "Costo de compra": item.costo_unitario,
                        "Total Linea": round(pendiente * item.costo_unitario, 2)
                    })
        
        if data:
            df_export = pd.DataFrame(data)
            # Asegurar que el directorio exista
            export_dir = BASE_DIR / "data" / "ordenes_de_compra"
            export_dir.mkdir(parents=True, exist_ok=True)
            
            # Guardar CSV
            df_export.to_csv(export_dir / "transito_consolidado.csv", index=False, encoding='utf-8-sig')
    except Exception as e:
        seguridad_logger.error(f"Error exportando CSV de tránsito: {e}", exc_info=True)
    finally:
        db.close()




@st.dialog("💸 Procesar Pago / Facturación")
def procesar_pago_dialog(po_dict):
    st.markdown(f"**Orden:** `{po_dict['id']}` | **Proveedor:** `{po_dict['vendor_nombre']}`")

    tipo_pago_ui = st.radio("Método de Facturación", ["Pago de Contado", "Crédito de Proveedor"])

    dias_credito = 0
    if tipo_pago_ui == "Crédito de Proveedor":
        dias_credito = st.number_input("Días de Crédito Acordados", min_value=1, step=1, value=15)

    c1, c2 = st.columns(2)
    fecha_pago = c1.date_input("Fecha de Emisión/Pago")
    referencia = c2.text_input("Referencia o N° Factura")

    comprobante = st.file_uploader("Adjuntar Comprobante/Factura (Max 10MB)", type=["pdf", "png", "jpg"])

    st.divider()
    if st.button("✅ Confirmar Transacción", width="stretch", type="primary"):
        if not comprobante:
            st.error("❌ El archivo de comprobante es obligatorio para auditoría financiera.")
        else:
            tipo_bd = "Crédito" if tipo_pago_ui == "Crédito de Proveedor" else "Contado"
            
            # Contenedor de estado dinámico (Enterprise UI)
            with st.status("Procesando transacción financiera...", expanded=True) as status:
                st.write("🔗 Verificando integridad de la orden...")
                time.sleep(0.5) # Breve pausa UI para sensación de procesamiento
                st.write("🧮 Recalculando Costo Promedio Ponderado (MAC)...")
                
                # Llamada actualizada a _mark_as_paid
                if _mark_as_paid(po_dict['id'], fecha_pago, referencia, comprobante, tipo_bd, dias_credito):
                    status.update(label="Transacción registrada exitosamente", state="complete", expanded=False)
                    st.toast("✅ Pago procesado y ledger actualizado.", icon="💸")
                    time.sleep(1.5)
                    st.rerun()
                else:
                    status.update(label="Fallo en la transacción", state="error", expanded=True)
                    st.error("Error crítico al asentar los datos financieros.")











def render_purchase_orders_view():
    st.markdown("### 🛒 Gestión de Órdenes de Compra")
    st.caption("Administra compras a proveedores, mercancía en tránsito y costeo dinámico de inventario.")

    opciones_po = ["📋 Historial de Órdenes", "➕ Crear Orden de Compra"]
    vista_seleccionada = st.pills("Sub-Navegación Órdenes", options=opciones_po, default=opciones_po[0], label_visibility="collapsed")
    st.divider()

    # --- PESTAÑA 1: HISTORIAL ---
    if vista_seleccionada == "📋 Historial de Órdenes":
        df_pos = _load_pos()

        if not df_pos.empty:
            def color_status(val):
                colors = {'Borrador': '#64748b', 'Esperado': '#eab308', 'Pagado': '#8b5cf6', 'Recibido Parcialmente': '#3b82f6', 'Completado': '#22c55e', 'Cancelado': '#ef4444'}
                return f"color: {colors.get(val, 'white')}; font-weight: bold;"

            st.dataframe(
                df_pos.style.map(color_status, subset=['Estado']), 
                width="stretch", hide_index=True,
                column_config={"Total": st.column_config.NumberColumn(format="$ %.2f")}
            )

            st.markdown("---")
            st.markdown("#### 🔍 Visualizar y Gestionar Orden")
            
            po_selected_id = st.selectbox("Selecciona una orden por ID para gestionarla:", df_pos['ID'].tolist())
            
            if po_selected_id:
                po = _load_po_details(po_selected_id)
                if po:

                    with st.expander(f"🧾 Orden {po['id']} | Proveedor: {po['vendor_nombre']}", expanded=True):
                        # --- PANEL VISUAL CORPORATIVO ---
                        st.markdown("##### 📊 Resumen Financiero y Logístico")
                        m1, m2, m3, m4 = st.columns(4)
                        m1.metric("Estado Actual", po['status'])
                        m2.metric("Total Orden", f"${po['total_orden']:,.2f}")
                        m3.metric("Costos de Envío/Aduana", f"${po['costos_adicionales_envio']:,.2f}")
                        m4.metric("Notas / Ref", po['notas'] if po['notas'] else "Sin notas")
                        
                        st.markdown("---")
                        st.markdown("##### 📦 Desglose de Artículos")
                        
                        items_data = []
                        for i in po['items']:
                            items_data.append({
                                "Item ID": i['id'], "Producto": i['producto_nombre'], "Pedido": i['cantidad'],
                                "Recibido": i['cantidad_recibida'], "Pendiente": i['cantidad'] - i['cantidad_recibida'],
                                "Costo Unit.": i['costo_unitario'], "Total": i['cantidad'] * i['costo_unitario']
                            })
                        
                        
                        
                        df_items_detail = pd.DataFrame(items_data)
                        
                        # --- MEJORA UI/UX: Función de Estilo ---
                        def color_status_celdas(val):
                            """Colorea de verde tenue las cantidades positivas y atenúa los ceros."""
                            try:
                                valor_num = float(val)
                                if valor_num > 0:
                                    return 'background-color: rgba(40, 167, 69, 0.15); color: #155724; font-weight: bold;'
                                elif valor_num == 0:
                                    return 'color: #6c757d;' # Gris neutro para ceros
                            except:
                                pass
                            return ''

                        # Aplicar estilo a las columnas de interés
                        df_estilizado = df_items_detail.drop(columns=["Item ID"]).style.map(
                            color_status_celdas, subset=["Pedido", "Recibido", "Pendiente"]
                        )
                        
                                                
                        # Formato bonito para la tabla de visualización
                        st.dataframe(
                            df_estilizado, 
                            hide_index=True, width="stretch",
                            column_config={
                                "Producto": st.column_config.TextColumn("Descripción del Producto", width="large"),
                                "Costo Unit.": st.column_config.NumberColumn("Costo Unit.", format="$%.2f"),
                                "Total": st.column_config.NumberColumn("Total", format="$%.2f")
                            }
                        )



                        st.markdown("<br>", unsafe_allow_html=True)
                        
                        # --- 1. LÓGICA PARA ÓRDENES ESPERADAS (Pagar) ---
                        if po['status'] == "Esperado":
                            
                            st.info("💳 Esta orden ha sido emitida. Registra la facturación para actualizar el Costo MAC.")
                            
                            col_action1, col_action2 = st.columns(2)

                            # Botón 1: Dispara el Modal de Pago
                            if col_action1.button("💸 Procesar Pago / Crédito", key=f"pay_{po['id']}", width="stretch", type="primary"):
                                procesar_pago_dialog(po)

                            # Botón 2: Generación del PDF
                            try:
                                # 🛡️ CORRECCIÓN: Pasamos la variable dinámica que acabas de crear
                                ruta_logo = os.path.join(os.path.dirname(__file__), "..", "..", "..", "assets", "logo.png")
                                pdf_bytes = _generar_pdf_orden(po, logo_path=ruta_logo)

                                col_action2.download_button(
                                    label="📄 Descargar PDF de Orden",
                                    data=pdf_bytes,
                                    file_name=f"{po['id']}_Orden_Compra.pdf",
                                    mime="application/pdf",
                                    width="stretch",
                                    key=f"pdf_{po['id']}"
                                )
                            except Exception as e:
                                col_action2.error("Error generando PDF.")
                                seguridad_logger.error(f"Error PDF {po['id']}: {e}", exc_info=True)

                            st.markdown("<br>", unsafe_allow_html=True)
                            if st.button("🚫 Cancelar Orden", width="stretch"):
                                if _cancel_po(po['id']): 
                                    if "pricing_raw_df" in st.session_state:
                                        del st.session_state["pricing_raw_df"]
                                    time.sleep(1.5)
                                    st.rerun()

                        # --- 2. LÓGICA PARA ÓRDENES PAGADAS (Recibir Físicamente) ---
                        elif po['status'] in ["Pagado", "Recibido Parcialmente"]:
                            st.write("Indica la cantidad física que está entrando a bodega HOY:")
                            df_items_detail["A Recibir HOY"] = df_items_detail["Pendiente"]
                            
                            edited_receipt = st.data_editor(
                                df_items_detail, hide_index=True, width="stretch",
                                column_config={
                                    "Item ID": None, "Producto": st.column_config.TextColumn(disabled=True),
                                    "Pedido": st.column_config.NumberColumn(disabled=True),
                                    "Recibido": st.column_config.NumberColumn(disabled=True),
                                    "Pendiente": st.column_config.NumberColumn(disabled=True),
                                    "Costo Unit.": st.column_config.NumberColumn(format="$%.2f", disabled=True),
                                    "A Recibir HOY": st.column_config.NumberColumn(min_value=0.0, step=1.0)
                                }
                            )

                            c_btn1, c_btn2 = st.columns(2)
                            if c_btn1.button("📦 Procesar Recepción Física", type="primary", width="stretch"):
                                to_receive = {row["Item ID"]: row["A Recibir HOY"] for _, row in edited_receipt.iterrows() if row["A Recibir HOY"] > 0}
                                if to_receive:
                                    if _process_reception(po['id'], to_receive):
                                        time.sleep(1.5)
                                        st.rerun()
                                else:
                                    st.warning("Indica alguna cantidad.")
                                    
                            if c_btn2.button("🚫 Cancelar Remanente", width="stretch"):
                                if _cancel_po(po['id']): 
                                    time.sleep(1.5)
                                    st.rerun()
                                
                        # --- 3. LÓGICA PARA BORRADORES (Emitir / Eliminar) ---
                        elif po['status'] == "Borrador":
                            c_btn1, c_btn2 = st.columns(2)
                            if c_btn1.button("🚀 Emitir Orden (Pasar a Esperado)", type="primary", width="stretch"):
                                db = SessionLocal()
                                try:
                                    po_to_emit = db.query(PurchaseOrder).filter_by(id=po['id']).first()
                                    po_to_emit.status = "Esperado"
                                    for item in po_to_emit.items:
                                        prod = db.query(Product).filter_by(id=item.product_id).first()
                                        if prod: prod.inventario_entrante = (prod.inventario_entrante or 0.0) + item.cantidad
                                    db.commit()
                                    st.success("Orden emitida con éxito.")
                                    time.sleep(1.5)
                                    st.rerun()
                                except Exception as e:
                                    db.rollback()
                                finally:
                                    db.close()
                                    
                            if c_btn2.button("🗑️ Eliminar Borrador", width="stretch"):
                                db = SessionLocal()
                                try:
                                    po_to_del = db.query(PurchaseOrder).filter_by(id=po['id']).first()
                                    db.delete(po_to_del)
                                    db.commit()
                                    st.success("Borrador eliminado.")
                                    time.sleep(1.5)
                                    st.rerun()
                                except Exception as e:
                                    db.rollback()
                                finally:
                                    db.close()
                                    
                        # --- 4. LÓGICA PARA COMPLETADOS/CANCELADOS (Solo Lectura) ---
                        else: 
                            pass                           
                        # --- 5. BOTÓN DUPLICAR (Disponible para cualquier estado) ---
                        st.markdown("<br>", unsafe_allow_html=True)
                        if st.button("📑 Duplicar Orden (Copiar al Carrito)"):
                            st.session_state.po_cart = []
                            for i in po['items']:
                                st.session_state.po_cart.append({
                                    "id": i['product_id'], "Producto": i['producto_nombre'], "En Stock": 0, "Entrantes": 0,
                                    "Costo Actual": i['costo_unitario'], "Cantidad a Pedir": i['cantidad'], "Costo Unitario": i['costo_unitario'], "Eliminar ❌": False
                                })
                            st.toast("Orden copiada. Ve a la pestaña 'Crear Orden de Compra'.", icon="✅")
        else:
            st.info("No hay órdenes en el historial.")
# --- PESTAÑA 2: CREAR ORDEN ---
    elif vista_seleccionada == "➕ Crear Orden de Compra":
        suppliers = _load_suppliers()
        df_catalog = _load_products_with_stock()

        if not suppliers:
            st.warning("⚠️ Debes registrar al menos un Proveedor en el Módulo de Finanzas.")
            return

        supplier_dict = {s['nombre']: s['id'] for s in suppliers}

        # 1. Inicializar el "Carrito de Compras" en la sesión
        if "po_cart" not in st.session_state:
            st.session_state.po_cart = []

        st.markdown("#### 1. Datos del Proveedor")
        c1, c2, c3 = st.columns(3)
        sup_name = c1.selectbox("Seleccionar Proveedor *", list(supplier_dict.keys()))
        fecha_esp = c2.date_input("Fecha Esperada")
        notas = c3.text_input("Notas / Referencia de Factura")

        st.markdown("---")
        st.markdown("#### 2. Artículos a Pedir")

        # 2. Buscador Reactivo (No se acumulan las pastillas arriba)
        col_search, col_add = st.columns([4, 1])
        prod_options = [""] + df_catalog['Producto'].tolist()
        selected_prod = col_search.selectbox("Buscar y seleccionar producto:", prod_options)
        
        if col_add.button("➕ Agregar a la tabla", width="stretch"):
            if selected_prod != "":
                # Evitar que se agregue dos veces el mismo producto
                if not any(item['Producto'] == selected_prod for item in st.session_state.po_cart):
                    prod_info = df_catalog[df_catalog['Producto'] == selected_prod].iloc[0]
                    st.session_state.po_cart.append({
                        "id": prod_info['id'],
                        "Producto": prod_info['Producto'],
                        "En Stock": prod_info['En Stock'],
                        "Entrantes": prod_info['Entrantes'],
                        "Costo Actual": prod_info['Costo Actual'],
                        "Cantidad a Pedir": 1.0,
                        "Costo Unitario": prod_info['Costo Actual'],
                        "Eliminar ❌": False # La columna para la 'X' que pediste
                    })
                st.rerun() # Refresca instantáneamente

        total_calculado = 0.0
        edited_df = pd.DataFrame()

        # 3. Renderizar la tabla de items si hay algo en el carrito
        if len(st.session_state.po_cart) > 0:
            df_cart = pd.DataFrame(st.session_state.po_cart)
            
            edited_df = st.data_editor(
                df_cart,
                key="editor_carrito_compras", # 🛡️ CORRECCIÓN 1: El "ancla" para que Streamlit no pierda tus números
                width="stretch",
                hide_index=True,
                column_config={
                    "id": None,
                    "Producto": st.column_config.TextColumn(disabled=True),
                    "En Stock": st.column_config.NumberColumn(disabled=True),
                    "Entrantes": st.column_config.NumberColumn(disabled=True),
                    "Costo Actual": st.column_config.NumberColumn(format="$ %.2f", disabled=True),
                    "Cantidad a Pedir": st.column_config.NumberColumn(min_value=0.1, step=1.0, required=True),
                    "Costo Unitario": st.column_config.NumberColumn(format="$ %.2f", min_value=0.0, required=True),
                    "Eliminar ❌": st.column_config.CheckboxColumn("Eliminar ❌", default=False)
                }
            )

            # 4. Lógica de Eliminación (La 'X' en la misma fila)
            if edited_df["Eliminar ❌"].any():
                # Filtrar y dejar solo los que NO están marcados para eliminar
                st.session_state.po_cart = edited_df[~edited_df["Eliminar ❌"]].to_dict('records')
                st.rerun()
            elif not df_cart.equals(edited_df):
                # 🛡️ CORRECCIÓN 2: Firewall. Solo sobrescribe la memoria si los números realmente cambiaron.
                st.session_state.po_cart = edited_df.to_dict('records')

            total_calculado = (edited_df["Cantidad a Pedir"] * edited_df["Costo Unitario"]).sum()
        else:
            st.info("👆 Busca un producto en la barra superior y haz clic en 'Agregar a la tabla'.")

        st.markdown("---")
        st.markdown("#### 3. Costos Adicionales y Resumen")
        c_envio, c_total = st.columns(2)
        costos_envio = c_envio.number_input("Gastos de Envío / Aduana ($)", min_value=0.0, step=10.0)

        gran_total = total_calculado + costos_envio
        c_total.info(f"### Total Estimado: **${gran_total:,.2f}**")

        st.markdown("<br>", unsafe_allow_html=True)
        col_b1, col_b2 = st.columns(2)
        
        # 5. Botones fuera del form (Reacción al instante)
        btn_borrador = col_b1.button("💾 Guardar como Borrador", width="stretch")
        btn_esperado = col_b2.button("🚀 Emitir Orden (Marcar como Esperado)", type="primary", width="stretch")

        if btn_borrador or btn_esperado:
            
            # 🛡️ 1. VALIDACIÓN UX: Interrupción amigable sin stacktraces
            if edited_df.empty or total_calculado <= 0:
                st.warning("✋ No puedes crear una Orden de Compra vacía o en cero. Por favor, agrega productos válidos a la tabla.", icon="🛒")
                st.stop() # Aborta la ejecución de Streamlit aquí mismo, protegiendo la BD de forma silenciosa

            status_operacion = "Esperado" if btn_esperado else "Borrador"
            
            # 🔄 2. FEEDBACK VISUAL: Micro-interacción de procesamiento
            with st.status(f"Registrando Orden como {status_operacion}...", expanded=True) as status:
                st.write("Verificando integridad de los datos...")
                time.sleep(0.5) # Pequeña pausa UX para que el usuario lea el mensaje
                st.write("Guardando en base de datos...")
                
                # Ejecución de la lógica de negocio
                exito = _save_po(
                    vendor_id=supplier_dict[sup_name],
                    fecha_esperada=fecha_esp,
                    notas=notas,
                    costos_envio=costos_envio,
                    df_items=edited_df,
                    status=status_operacion
                )
                
                if exito:
                    status.update(label="Orden creada exitosamente", state="complete", expanded=False)
                    st.toast("✅ Orden de compra generada y guardada.", icon="📝")
                    st.session_state.po_cart = []
                    time.sleep(1.5) # Tiempo suficiente para que el usuario vea el Toast
                    st.rerun()
                else:
                    status.update(label="Fallo en la creación de la orden", state="error", expanded=True)