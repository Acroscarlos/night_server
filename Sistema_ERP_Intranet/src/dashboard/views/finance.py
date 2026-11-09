#!/usr/bin/env python
# coding: utf-8

"""
Módulo Financiero (Refactorizado V7.2 - ERP Integrado)
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import json
import uuid
import os
import time
from werkzeug.utils import secure_filename
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field, ValidationError, field_validator

from src.database.db_core import engine, SessionLocal
from src.database.models import Vendor, LedgerTransaction, AuditLog
from src.logger_config import get_custom_logger

from sqlalchemy import func # Asegúrate de que func esté importado
from sqlalchemy.exc import IntegrityError
finanzas_logger = get_custom_logger("finanzas", "modulo_finanzas.log")

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DATA_DIR = BASE_DIR / "data"
DOCS_DIR = DATA_DIR / "docs" / "finance"
DOCS_DIR.mkdir(parents=True, exist_ok=True)

# ==========================================
# UTILIDAD DE AUDITORÍA
# ==========================================
def _get_current_user() -> str:
    return st.session_state.get("username", "system_user")

# 1. Definimos el contrato de datos (Esquema estricto)
class AuditDetailsSchema(BaseModel):
    modulo: str = Field(default="Finanzas", description="Módulo de ejecución")
    datos_registro: Dict[str, Any] = Field(..., description="Payload crudo de la transacción")



# ==========================================
# FIREWALL DE INYECCIÓN (Vector 4)
# ==========================================
class ManualTransactionSchema(BaseModel):
    monto: float
    fecha: date
    vencimiento: date
    tipo: str

    @field_validator('monto')
    def validate_monto_positivo(cls, v):
        if v <= 0:
            raise ValueError("El monto de la transacción debe ser estrictamente mayor a 0.")
        return v

    @field_validator('vencimiento')
    def validate_rango_fechas(cls, v):
        limite_superior = date.today() + timedelta(days=365 * 5) # Máx 5 años al futuro
        limite_inferior = date.today() - timedelta(days=365 * 5)  # Máx 5 años al pasado
        if not (limite_inferior <= v <= limite_superior):
            raise ValueError(f"La fecha de vencimiento está fuera del rango lógico permitido (± 5 años).")
        return v

    @field_validator('tipo')
    def validate_tipo(cls, v):
        permitidos = ["Cuentas por Pagar", "Cuentas por Cobrar"]
        if v not in permitidos:
            raise ValueError(f"Tipo de transacción desconocido: {v}")
        return v



# 2. Reemplazamos _log_audit con validación Pydantic
def _log_audit(db_session, tabla: str, registro_id: str, accion: str, detalles_dict: dict):
    try:
        # 🛡️ FIREWALL: Envolvemos tu diccionario original en el esquema de seguridad
        payload_seguro = {
            "modulo": "Finanzas",
            "datos_registro": detalles_dict
        }
        # Pydantic valida que la estructura sea correcta y sanitiza los tipos
        detalles_validados = AuditDetailsSchema(**payload_seguro)
        
        # Extraemos el dict limpio (compatible con Pydantic v1 y v2)
        clean_details = detalles_validados.model_dump() if hasattr(detalles_validados, 'model_dump') else detalles_validados.dict()

        audit_entry = AuditLog(
            tabla_afectada=tabla,
            registro_id=str(registro_id),
            accion=accion,
            usuario=_get_current_user(),
            detalles_json=clean_details
        )
        db_session.add(audit_entry)
        
    except ValidationError as e:
        # Si entra un dato corrupto, lo atrapa aquí sin tumbar la aplicación
        finanzas_logger.error(f"❌ Estructura JSON inválida para auditoría en tabla {tabla}: {e}")
    except Exception as e:
        finanzas_logger.error(f"Fallo al registrar auditoría en la BD: {e}", exc_info=True)
        st.error(f"Error generando log de auditoría: {e}")


def _save_receipt(uploaded_file, transaction_id: str):
    """Guarda comprobantes físicos de manera segura previniendo Path Traversal."""
    if uploaded_file is None: return
        
    tx_folder = DOCS_DIR / str(transaction_id)
    tx_folder.mkdir(parents=True, exist_ok=True)
    
    # Sanitización estricta
    filename = secure_filename(uploaded_file.name)
    if not filename:
        filename = f"comprobante_{int(time.time())}.pdf"
        
    file_path = tx_folder / filename
    with open(file_path, "wb") as f:
        f.write(uploaded_file.getbuffer())




# ==========================================
# CAPA DE DATOS
# ==========================================
def _load_vendors() -> List[Dict]:
    try:
        with engine.connect() as conn:
            df = pd.read_sql("SELECT * FROM vendors", conn)
        return df.to_dict(orient="records")
    except Exception:
        return []

def _save_vendors(vendors_list: List[Dict]):
    db = SessionLocal()
    try:
        current_user = _get_current_user()
        for data in vendors_list:
            vendor_id = data.get('id')
            existente = db.query(Vendor).filter(Vendor.id == vendor_id).first()

            if not existente:
                nuevo = Vendor(
                    id=vendor_id,
                    nombre=data.get('nombre'),
                    rif=data.get('rif'),
                    contacto=data.get('contacto'),
                    email=data.get('email'),
                    telefono=data.get('telefono'),
                    terminos=data.get('terminos'),
                    created_by=current_user
                )
                db.add(nuevo)
                _log_audit(db, "vendors", nuevo.id, "INSERT", data)
            else:
                existente.nombre = data.get('nombre', existente.nombre)
                existente.rif = data.get('rif', existente.rif)
                existente.contacto = data.get('contacto', existente.contacto)
                existente.email = data.get('email', existente.email)
                existente.telefono = data.get('telefono', existente.telefono)
                existente.terminos = data.get('terminos', existente.terminos)
                existente.created_by = current_user
                _log_audit(db, "vendors", existente.id, "UPDATE", data)
        db.commit()
        finanzas_logger.info(f"Se actualizaron exitosamente los datos de {len(vendors_list)} proveedor(es).")
    except Exception as e:
        db.rollback()
        finanzas_logger.error(f"Error crítico guardando proveedores: {str(e)}", exc_info=True)
        st.error(f"Error crítico guardando proveedores: {str(e)}")
    finally:
        db.close()

def _load_ledger() -> pd.DataFrame:
    try:
        query = """
            SELECT l.id, l.fecha, l.tipo, l.entidad_id, v.nombre as entidad, 
                   l.monto, l.vencimiento, l.notas, l.status
            FROM ledger_transactions l
            LEFT JOIN vendors v ON l.entidad_id = v.id
        """
        with engine.connect() as conn:
            df = pd.read_sql(query, conn)
        
        if not df.empty:
            df['vencimiento'] = pd.to_datetime(df['vencimiento']).dt.date
            df['fecha'] = pd.to_datetime(df['fecha']).dt.date
            df['monto'] = pd.to_numeric(df['monto'], errors='coerce').fillna(0.0)
        else:
             df = pd.DataFrame(columns=["id", "fecha", "tipo", "entidad_id", "entidad", "monto", "vencimiento", "notas", "status"])
        return df
    except Exception:
        return pd.DataFrame(columns=["id", "fecha", "tipo", "entidad_id", "entidad", "monto", "vencimiento", "notas", "status"])

def _save_ledger(df: pd.DataFrame, uploaded_file=None):
    """Guarda transacciones aplicando bloqueos optimistas e inmutabilidad."""
    db = SessionLocal()
    try:
        current_user = _get_current_user()
        ultimo_id_procesado = None

        for _, row in df.iterrows():
            data = row.to_dict()
            tx_id = data.get('id')
            parse_date = lambda d: pd.to_datetime(d).to_pydatetime() if pd.notnull(d) else None
            
            # 🛡️ BARRERA 1: ROW LOCK. Congelamos la fila para evitar que dos usuarios la editen a la vez
            tx = db.query(LedgerTransaction).filter(LedgerTransaction.id == tx_id).with_for_update().first()

            if not tx:
                # --- TU LÓGICA INTACTA: Creación de nuevo registro ---
                nueva_tx = LedgerTransaction(
                    id=tx_id, fecha=parse_date(data.get('fecha')), tipo=data.get('tipo'),
                    entidad_id=data.get('entidad_id'), monto=float(data.get('monto', 0.0)),
                    vencimiento=parse_date(data.get('vencimiento')), notas=data.get('notas'),
                    status=data.get('status'), created_by=current_user
                )
                db.add(nueva_tx)
                ultimo_id_procesado = str(tx_id)
            else:
                # 🛡️ BARRERA 2: INMUTABILIDAD. Si se anula, dejamos rastro auditable
                estado_anterior = tx.status
                nuevo_estado = data.get('status', tx.status)
                
                if estado_anterior != "Anulado" and nuevo_estado == "Anulado":
                    tx.notas = f"{data.get('notas', tx.notas)} | ANULADA POR {current_user}"
                    _log_audit(db, "ledger_transactions", tx.id, "ANULACIÓN", {"motivo": "Anulado desde UI", "estado_anterior": estado_anterior})
                else:
                    tx.notas = data.get('notas', tx.notas)

                # --- TU LÓGICA INTACTA: Actualización de registro ---
                tx.fecha = parse_date(data.get('fecha')) or tx.fecha
                tx.tipo = data.get('tipo', tx.tipo)
                tx.monto = float(data.get('monto', tx.monto))
                tx.vencimiento = parse_date(data.get('vencimiento')) or tx.vencimiento
                tx.status = nuevo_estado
                tx.created_by = current_user
                ultimo_id_procesado = str(tx.id)
                
        # 1. COMMIT A BASE DE DATOS
        db.commit()
        finanzas_logger.info(f"Transacción {ultimo_id_procesado} guardada con éxito bajo bloqueo transaccional.")
        
        # 2. GUARDADO FÍSICO (Garantizado sin huérfanos - TU LÓGICA INTACTA)
        if uploaded_file and ultimo_id_procesado:
            _save_receipt(uploaded_file, ultimo_id_procesado)
            finanzas_logger.info(f"Comprobante físico adjuntado para la transacción {ultimo_id_procesado}.")
            
    except Exception as e:
        db.rollback()
        finanzas_logger.error(f"Error crítico en Libro Mayor: {str(e)}", exc_info=True)
        st.error(f"Error crítico en Libro Mayor: {str(e)}")
    finally:
        db.close()


def _get_30_day_cashflow_projections() -> pd.DataFrame:
    """Extrae proyecciones a 30 días sumadas y agrupadas nativamente por SQLite (O(1))."""
    db = SessionLocal()
    try:
        hoy = date.today()
        limite_30_dias = hoy + timedelta(days=30)

        # 🛡️ DELEGACIÓN AL MOTOR SQL: Cero sobrecarga en Pandas
        query = db.query(
            func.date(LedgerTransaction.vencimiento).label('vencimiento_date'),
            LedgerTransaction.tipo,
            func.sum(LedgerTransaction.monto).label('monto_total')
        ).filter(
            LedgerTransaction.status == "Pendiente",
            func.date(LedgerTransaction.vencimiento) >= hoy,
            func.date(LedgerTransaction.vencimiento) <= limite_30_dias
        ).group_by(
            func.date(LedgerTransaction.vencimiento), LedgerTransaction.tipo
        ).order_by(
            func.date(LedgerTransaction.vencimiento).asc()
        )
        return pd.read_sql(query.statement, db.bind)
    except Exception as e:
        finanzas_logger.error(f"Error generando proyecciones: {e}")
        return pd.DataFrame()
    finally:
        db.close()

def _insert_single_transaction(tipo: str, entidad_id: str, monto: float, vencimiento, notas: str, uploaded_file=None):
    """Inserta una operación de forma atómica pasando por el Firewall Pydantic."""
    db = SessionLocal()
    try:
        # 🛡️ BARRERA PYDANTIC (Vector 4)
        datos_limpios = ManualTransactionSchema(
            monto=monto, fecha=date.today(), vencimiento=vencimiento, tipo=tipo
        )

        new_id = str(uuid.uuid4())
        nueva_tx = LedgerTransaction(
            id=new_id, fecha=datetime.combine(datos_limpios.fecha, datetime.min.time()), 
            tipo=datos_limpios.tipo, entidad_id=entidad_id, 
            monto=datos_limpios.monto, 
            vencimiento=datetime.combine(datos_limpios.vencimiento, datetime.min.time()), 
            notas=notas, status="Pendiente", created_by=_get_current_user()
        )
        db.add(nueva_tx)
        
        # Guardado en Auditoría Inmutable
        _log_audit(db, "ledger_transactions", new_id, "INSERT_MANUAL", {"tipo": tipo, "monto": monto, "entidad_id": entidad_id})
        
        db.commit()
        if uploaded_file: _save_receipt(uploaded_file, new_id)
        finanzas_logger.info(f"Operación manual {new_id} registrada.")
        return True
    except ValidationError as ve:
        # Si Pydantic detecta un error (ej. monto negativo), abortamos amigablemente
        st.error(f"❌ Datos inválidos: {ve.errors()[0]['msg']}")
        return False
    except Exception as e:
        db.rollback()
        st.error("❌ Error interno registrando la operación.")
        return False
    finally:
        db.close()
# ==========================================
# INTERFAZ DE USUARIO
# ==========================================
def render_finance_view():
    st.markdown("### 💰 Sistema Integrado de Finanzas")

    opciones_finanzas = [
        "📊 Dashboard Ejecutivo",
        "📒 Libro Mayor",
        "🏢 Proveedores & Clientes",
        "➕ Registrar Operación",
        "🕵️‍♂️ Historial de Auditoría"
    ]
    vista_seleccionada = st.pills("Sub-Navegación", options=opciones_finanzas, default=opciones_finanzas[0], label_visibility="collapsed")
    st.divider()

    # ---------------------------------------------------------
    # TAB 1: DASHBOARD EJECUTIVO
    # ---------------------------------------------------------
    if vista_seleccionada == "📊 Dashboard Ejecutivo":
        df = _load_ledger()
        if df.empty:
            st.info("Sin datos financieros registrados.")
        else:
            df['vencimiento'] = pd.to_datetime(df['vencimiento'], errors='coerce')
            today = pd.Timestamp('today').normalize()
            
            pending_pay = df[(df['tipo'] == 'Cuentas por Pagar') & (df['status'] == 'Pendiente')]['monto'].sum()
            pending_rec = df[(df['tipo'] == 'Cuentas por Cobrar') & (df['status'] == 'Pendiente')]['monto'].sum()
            overdue_amount = df[(df['status'] == 'Pendiente') & (df['vencimiento'] < today)]['monto'].sum()
            cash_flow_neto = pending_rec - pending_pay

            # --- KPIs EJECUTIVOS ---
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Cuentas por Cobrar (AR)", f"${pending_rec:,.2f}", delta="Activo Corriente", delta_color="normal")
            c2.metric("Cuentas por Pagar (AP)", f"${pending_pay:,.2f}", delta="Pasivo Corriente", delta_color="inverse")
            
            balance_color = "normal" if cash_flow_neto >= 0 else "inverse"
            c3.metric("Balance Neto Proyectado", f"${cash_flow_neto:,.2f}", delta="Flujo de Caja", delta_color=balance_color)
            c4.metric("⚠️ Deuda Vencida (Alarma)", f"${overdue_amount:,.2f}", delta_color="inverse")
            st.markdown("---")

            # --- 🛡️ VECTOR 3: GRÁFICO CQRS O(1) ---
            st.subheader("📊 Calendario de Vencimientos (Próximos 30 Días)")
            df_proj = _get_30_day_cashflow_projections()

            if not df_proj.empty:
                col_chart1, col_chart2 = st.columns([7, 3])
                
                # Gráfico 1: Barras Verticales por Día
                fig = px.bar(
                    df_proj, x="vencimiento_date", y="monto_total", color="tipo", barmode="group",
                    labels={"vencimiento_date": "Fecha de Vencimiento", "monto_total": "Monto ($)"},
                    color_discrete_map={"Cuentas por Pagar": "#EF553B", "Cuentas por Cobrar": "#00CC96"},
                    text_auto='.2s', title="Flujo de Caja Diario Exacto"
                )
                fig.update_layout(xaxis_tickangle=-45, hovermode="x unified")
                col_chart1.plotly_chart(fig, use_container_width=True)
                
                # Gráfico 2: Distribución (Donut Chart)
                df_donut = df_proj.groupby('tipo')['monto_total'].sum().reset_index()
                fig_pie = px.pie(
                    df_donut, values='monto_total', names='tipo', hole=0.4,
                    color_discrete_map={"Cuentas por Pagar": "#EF553B", "Cuentas por Cobrar": "#00CC96"},
                    title="Proporción a 30 Días"
                )
                fig_pie.update_traces(textposition='inside', textinfo='percent+label')
                col_chart2.plotly_chart(fig_pie, use_container_width=True)
            else:
                st.success("✅ Excelente salud financiera: No hay facturas pendientes en los próximos 30 días.")

    # ---------------------------------------------------------
    # TAB 2: LIBRO MAYOR
    # ---------------------------------------------------------
    elif vista_seleccionada == "📒 Libro Mayor":
        st.markdown("#### 📒 Maestro de Movimientos")
        df_ledger = _load_ledger()
        if not df_ledger.empty:
            with st.container(border=True):
                col_f1, col_f2, col_f3, col_f4 = st.columns([2, 1.5, 1.5, 1])
                d_range = col_f1.date_input("Rango de Fechas", [])
                sel_types = col_f2.multiselect("Tipo", list(df_ledger['tipo'].unique()), default=list(df_ledger['tipo'].unique()))
                sel_statuses = col_f3.multiselect("Estatus", list(df_ledger['status'].unique()), default=list(df_ledger['status'].unique()))
                
                csv = df_ledger.to_csv(index=False).encode('utf-8')
                col_f4.download_button("⬇️ Exportar", data=csv, file_name=f"ledger_{date.today()}.csv", mime="text/csv", width="stretch")

            mask = (df_ledger['tipo'].isin(sel_types)) & (df_ledger['status'].isin(sel_statuses))
            if len(d_range) == 2:
                mask = mask & (df_ledger['fecha'] >= d_range[0]) & (df_ledger['fecha'] <= d_range[1])

            df_filtered = df_ledger[mask].sort_values("vencimiento")

            edited_df = st.data_editor(
                df_filtered,
                column_config={
                    "id": None, "entidad_id": None,
                    "monto": st.column_config.NumberColumn(format="$%.2f"),
                    "vencimiento": st.column_config.DateColumn(format="DD/MM/YYYY"),
                    "fecha": st.column_config.DateColumn(format="DD/MM/YYYY"),
                    "status": st.column_config.SelectboxColumn(options=["Pendiente", "Pagado", "Anulado"], required=True),
                    "tipo": st.column_config.TextColumn(disabled=True),
                    "entidad": st.column_config.TextColumn("Entidad", disabled=True),
                },
                width='stretch', hide_index=True, key="ledger_editor"
            )

            if not edited_df.equals(df_filtered):
                _save_ledger(edited_df)
                st.rerun()

    # ---------------------------------------------------------
    # TAB 3: GESTIÓN DE PROVEEDORES (COMPLETA)
    # ---------------------------------------------------------
    elif vista_seleccionada == "🏢 Proveedores & Clientes":
        st.markdown("#### 🏢 Base de Datos de Proveedores & Clientes")
        vendors = _load_vendors()

        if vendors:
            df_v = pd.DataFrame(vendors).drop(columns=['id', 'created_by', 'created_at', 'updated_at'], errors='ignore')
            st.dataframe(df_v, width='stretch', column_config={"email": st.column_config.LinkColumn("Email", display_text="Enviar Correo")})

        c_add, c_edit = st.columns(2)

        with c_add:
            with st.expander("➕ Agregar Nuevo Proveedor", expanded=False):
                with st.form("add_vendor_form", clear_on_submit=True):
                    v_name = st.text_input("Nombre Empresa *")
                    v_tax = st.text_input("RIF / Fiscal ID")
                    v_contact = st.text_input("Persona Contacto")
                    v_email = st.text_input("Email")
                    v_phone = st.text_input("Teléfono")
                    
                    # 🛡️ MEJORA: Días de crédito 100% personalizables
                    v_dias_credito = st.number_input("Días de Crédito (0 = Contado)", min_value=0, max_value=365, step=1, value=0, help="Introduce 0 para pagos de contado, o el número exacto de días (ej. 120) para crédito.")
                    v_terms = "Contado" if v_dias_credito == 0 else f"{v_dias_credito} días"

                    if st.form_submit_button("Guardar Proveedor"):
                        if v_name:
                            vendors.append({
                                "id": str(uuid.uuid4()), "nombre": v_name, "rif": v_tax,
                                "contacto": v_contact, "email": v_email, "telefono": v_phone, "terminos": v_terms
                            })
                            _save_vendors(vendors)
                            st.rerun()

        with c_edit:
            with st.expander("✏️ Editar Proveedor (Full)", expanded=False):
                if vendors:
                    vendor_names = [v['nombre'] for v in vendors]
                    selected_name = st.selectbox("Seleccionar Proveedor a Editar", vendor_names)
                    selected_vendor = next(v for v in vendors if v['nombre'] == selected_name)

                    with st.form("edit_vendor_form"):
                        # MEJORA: Todos los campos son editables ahora
                        new_name = st.text_input("Nombre Empresa", value=selected_vendor.get('nombre', ''))
                        new_tax = st.text_input("RIF / Fiscal ID", value=selected_vendor.get('rif', ''))
                        new_contact = st.text_input("Contacto", value=selected_vendor.get('contacto', ''))
                        new_email = st.text_input("Email", value=selected_vendor.get('email', ''))
                        new_phone = st.text_input("Teléfono", value=selected_vendor.get('telefono', ''))
                        
                        # 🛡️ MEJORA: Extracción inteligente de días para edición
                        current_term = str(selected_vendor.get('terminos', 'Contado'))
                        current_dias = 0
                        
                        # Extraemos solo los números del texto (ej. de "120 días" saca 120)
                        digitos = ''.join(filter(str.isdigit, current_term))
                        if digitos:
                            current_dias = int(digitos)
                            
                        new_dias_credito = st.number_input("Días de Crédito (0 = Contado)", min_value=0, max_value=365, step=1, value=current_dias)
                        new_terms = "Contado" if new_dias_credito == 0 else f"{new_dias_credito} días"

                        if st.form_submit_button("Actualizar Todos los Datos"):
                            if new_name:
                                selected_vendor['nombre'] = new_name
                                selected_vendor['rif'] = new_tax
                                selected_vendor['contacto'] = new_contact
                                selected_vendor['email'] = new_email
                                selected_vendor['telefono'] = new_phone
                                selected_vendor['terminos'] = new_terms
                                _save_vendors([selected_vendor]) 
                                st.rerun()
                            else:
                                st.error("El nombre no puede estar vacío.")

    # ---------------------------------------------------------
    # TAB 4: REGISTRAR OPERACIÓN
    # ---------------------------------------------------------
    elif vista_seleccionada == "➕ Registrar Operación":
        st.markdown("#### 🧾 Alta de Operación Financiera")
        vendors_list = _load_vendors()
        vendor_options = {v['nombre']: v['id'] for v in vendors_list}

        if not vendor_options:
            st.error("🚫 Registre un Proveedor en la pestaña anterior primero.")
        else:
            with st.form("new_transaction_form", clear_on_submit=True):
                # --- TU UI INTACTA ---
                col_a, col_b = st.columns(2)
                with col_a:
                    tipo = st.selectbox("Tipo", ["Cuentas por Pagar", "Cuentas por Cobrar"])
                    entidad_nombre = st.selectbox("Entidad", list(vendor_options.keys()))
                    monto = st.number_input("Monto ($)", min_value=0.01, step=10.0)
                with col_b:
                    vencimiento = st.date_input("Vencimiento", value=date.today() + timedelta(days=30))
                    notas = st.text_area("Notas")
                    uploaded_file = st.file_uploader("📎 Comprobante", type=['pdf', 'jpg', 'png'])

                if st.form_submit_button("💾 Registrar"):
                    # 🛡️ SOLUCIÓN: Usamos inserción atómica en lugar de pd.concat()
                    finanzas_logger.info(f"Usuario {_get_current_user()} intentando registrar {tipo} por ${monto} a la entidad {entidad_nombre}.")
                    
                    exito = _insert_single_transaction(
                        tipo=tipo, 
                        entidad_id=vendor_options[entidad_nombre], 
                        monto=monto, 
                        vencimiento=vencimiento, 
                        notas=notas, 
                        uploaded_file=uploaded_file
                    )
                    
                    if exito:
                        st.success("✅ Transacción registrada. Libro Mayor actualizado.")
                        time.sleep(1.5)
                        st.rerun()

    # ---------------------------------------------------------
    # TAB 5: AUDITORÍA (NUEVA PESTAÑA)
    # ---------------------------------------------------------
    elif vista_seleccionada == "🕵️‍♂️ Historial de Auditoría":
        st.markdown("#### 🕵️‍♂️ Registro de Auditoría y Trazabilidad")
        st.caption("Cualquier inserción o edición de facturas y proveedores se registra de forma inmutable aquí.")
        try:
            with engine.connect() as conn:
                df_audit = pd.read_sql("SELECT id, fecha_hora, usuario, accion, tabla_afectada, detalles_json FROM audit_logs ORDER BY fecha_hora DESC LIMIT 100", conn)
            
            if not df_audit.empty:
                st.dataframe(
                    df_audit,
                    width='stretch',
                    hide_index=True,
                    column_config={
                        "detalles_json": st.column_config.TextColumn("Detalle (JSON)"),
                        "fecha_hora": st.column_config.DatetimeColumn("Fecha y Hora", format="YYYY-MM-DD HH:mm:ss")
                    }
                )
            else:
                st.info("No hay registros de auditoría aún.")
        except Exception:
            st.warning("La tabla de auditoría se inicializará con tu primera operación.")