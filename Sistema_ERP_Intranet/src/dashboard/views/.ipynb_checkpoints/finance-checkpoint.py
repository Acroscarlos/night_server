#!/usr/bin/env python
# coding: utf-8

# Aquí tienes el código completo y refactorizado para src/dashboard/views/finance.py.
# He actuado como Senior Fintech Developer, implementando un sistema robusto con auditoría, manejo de estados financieros y una experiencia de usuario fluida (UX) tipo Excel.
# Características Clave Implementadas:
# 1. Edición "Excel-like": Uso de st.data_editor con configuración de columnas (Dropdowns para Status, Calendarios para Fechas).
# 2. Persistencia Automática: Detecta cambios en la tabla y actualiza el CSV al instante.
# 3. Auditoría (Audit Trail): Sistema de gestión de archivos adjuntos vinculado a UUIDs únicos por transacción.
# 4. Inteligencia de Negocio: Tablero de control para proveedores con cálculo de deuda vencida y proyecciones de flujo de caja.
# 
# separando estrictamente la capa de datos (Backend) de la capa de presentación (Frontend). Se implementó un sistema CRUD (Create, Read, Update, Delete) completo para proveedores y se integró dinámicamente en el formulario de operaciones para asegurar la integridad referencial de los datos.

# In[ ]:


import stream
import streamlit as st
import pandas as pd
import plotly.express as px
import json
import uuid
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

# --- CONFIGURACIÓN DE RUTAS Y DIRECTORIOS ---
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DATA_DIR = BASE_DIR / "data"
DOCS_DIR = DATA_DIR / "docs" / "finance"
LEDGER_FILE = DATA_DIR / "finance_ledger.csv"
VENDORS_FILE = DATA_DIR / "vendors.json"

# Asegurar estructura de directorios
for d in [DATA_DIR, DOCS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ==========================================
# CAPA DE DATOS: GESTIÓN DE PROVEEDORES (VMS)
# ==========================================

def _load_vendors() -> List[Dict]:
    """Carga la base de datos de proveedores desde JSON."""
    if not VENDORS_FILE.exists():
        return []
    try:
        with open(VENDORS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError:
        return []

def _save_vendors(vendors_list: List[Dict]):
    """Persistencia atómica de proveedores."""
    temp_file = VENDORS_FILE.with_suffix('.tmp')
    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump(vendors_list, f, indent=4, ensure_ascii=False)
    os.replace(temp_file, VENDORS_FILE)

def _get_vendor_names() -> List[str]:
    """Helper para obtener solo nombres para dropdowns."""
    vendors = _load_vendors()
    return sorted([v['company_name'] for v in vendors])

# ==========================================
# CAPA DE DATOS: LIBRO MAYOR (LEDGER)
# ==========================================

def _load_ledger() -> pd.DataFrame:
    """Carga y normaliza el Libro Mayor."""
    if not LEDGER_FILE.exists():
        return pd.DataFrame(columns=[
            "ID", "Fecha", "Tipo", "Entidad", "Monto", "Vencimiento", "Notas", "Status"
        ])

    df = pd.read_csv(LEDGER_FILE)

    # Normalización de Tipos de Datos
    if "ID" not in df.columns:
        df["ID"] = [str(uuid.uuid4()) for _ in range(len(df))]

    # Conversión segura de fechas
    df['Vencimiento'] = pd.to_datetime(df['Vencimiento']).dt.date
    df['Fecha'] = pd.to_datetime(df['Fecha']).dt.date
    df['Monto'] = pd.to_numeric(df['Monto'], errors='coerce').fillna(0.0)

    # Asegurar columna Status para el editor
    if "Status" not in df.columns:
        df["Status"] = "Pendiente"

    return df

def _save_ledger(df: pd.DataFrame):
    """Guarda cambios en el Ledger."""
    temp_file = LEDGER_FILE.with_suffix(".tmp")
    df.to_csv(temp_file, index=False)
    os.replace(temp_file, LEDGER_FILE)

def _save_receipt(uploaded_file, transaction_id: str):
    """Guarda comprobantes físicos."""
    if uploaded_file is None: return
    tx_folder = DOCS_DIR / transaction_id
    tx_folder.mkdir(exist_ok=True)
    file_path = tx_folder / uploaded_file.name
    with open(file_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

# ==========================================
# INTERFAZ DE USUARIO (VISTA PRINCIPAL)
# ==========================================

def render_finance_view():
    st.markdown("### 💰 Sistema Integrado de Finanzas (ERP)")

    # Navegación Principal
    tab_dash, tab_ledger, tab_vendors, tab_new = st.tabs([
        "📊 Dashboard Ejecutivo",
        "📒 Libro Mayor & Auditoría",
        "🏢 Gestión de Proveedores",
        "➕ Registrar Operación"
    ])

    # ---------------------------------------------------------
    # TAB 1: DASHBOARD (Resumen Ejecutivo)
    # ---------------------------------------------------------
    with tab_dash:
        df = _load_ledger()
        if df.empty:
            st.info("Sin datos financieros registrados.")
        else:
            # 1. KPIs Superiores
            pending_pay = df[(df['Tipo'] == 'Cuentas por Pagar') & (df['Status'] == 'Pendiente')]['Monto'].sum()
            pending_rec = df[(df['Tipo'] == 'Cuentas por Cobrar') & (df['Status'] == 'Pendiente')]['Monto'].sum()

            # Cálculo de vencidos
            today = date.today()
            overdue_mask = (df['Status'] == 'Pendiente') & (df['Vencimiento'] < today)
            overdue_amount = df[overdue_mask]['Monto'].sum()

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Cuentas por Pagar (AP)", f"${pending_pay:,.2f}", delta="Pasivo Corriente", delta_color="inverse")
            c2.metric("Cuentas por Cobrar (AR)", f"${pending_rec:,.2f}", delta="Activo Corriente")
            c3.metric("Cash Flow Proyectado", f"${(pending_rec - pending_pay):,.2f}")
            c4.metric("⚠️ Deuda Vencida", f"${overdue_amount:,.2f}", delta_color="inverse")

            st.divider()

            # 2. Gráfico de Flujo de Caja
            st.subheader("📉 Proyección de Vencimientos (4 Semanas)")
            future_limit = today + timedelta(weeks=4)
            mask_future = (df['Status'] == 'Pendiente') & (df['Vencimiento'] >= today) & (df['Vencimiento'] <= future_limit)

            df_proj = df[mask_future].copy()
            if not df_proj.empty:
                df_proj['Semana'] = pd.to_datetime(df_proj['Vencimiento']).dt.isocalendar().week
                df_chart = df_proj.groupby(['Semana', 'Tipo'])['Monto'].sum().reset_index()

                fig = px.bar(
                    df_chart, x="Semana", y="Monto", color="Tipo", barmode="group",
                    color_discrete_map={"Cuentas por Pagar": "#EF553B", "Cuentas por Cobrar": "#00CC96"},
                    text_auto='.2s'
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.caption("No hay vencimientos pendientes próximos.")

    # ---------------------------------------------------------
    # TAB 2: LIBRO MAYOR (Tabla Full-Width + Filtros)
    # ---------------------------------------------------------
    with tab_ledger:
        st.markdown("#### 📒 Maestro de Movimientos")

        df_ledger = _load_ledger()
        if df_ledger.empty:
            st.warning("El libro mayor está vacío.")
        else:
            # A. Barra de Herramientas (Filtros Avanzados)
            with st.container(border=True):
                col_f1, col_f2, col_f3, col_f4 = st.columns([2, 1.5, 1.5, 1])

                # Filtro Fecha
                d_range = col_f1.date_input("Rango de Fechas", [])

                # Filtro Tipo
                types = list(df_ledger['Tipo'].unique())
                sel_types = col_f2.multiselect("Tipo", types, default=types)

                # Filtro Status
                statuses = list(df_ledger['Status'].unique())
                sel_statuses = col_f3.multiselect("Estatus", statuses, default=statuses)

                # Botón Exportar
                csv = df_ledger.to_csv(index=False).encode('utf-8')
                col_f4.download_button(
                    label="⬇️ Exportar CSV",
                    data=csv,
                    file_name=f"ledger_export_{date.today()}.csv",
                    mime="text/csv",
                    use_container_width=True
                )

            # Aplicar Filtros
            mask = (df_ledger['Tipo'].isin(sel_types)) & (df_ledger['Status'].isin(sel_statuses))
            # Corrección: Solo filtrar por fecha si el usuario seleccionó un rango completo (Inicio y Fin)
            if len(d_range) == 2:
                mask = mask & (df_ledger['Fecha'] >= d_range[0]) & (df_ledger['Fecha'] <= d_range[1])

            df_filtered = df_ledger[mask].sort_values("Vencimiento")

            # B. Tabla Editable (Excel-like)
            edited_df = st.data_editor(
                df_filtered,
                column_config={
                    "ID": st.column_config.TextColumn(disabled=True, width="small"),
                    "Monto": st.column_config.NumberColumn(format="$%.2f"),
                    "Vencimiento": st.column_config.DateColumn(format="DD/MM/YYYY"),
                    # --- CORRECCIÓN CRÍTICA APLICADA AQUÍ ---
                    "Status": st.column_config.SelectboxColumn(
                        options=["Pendiente", "Pagado", "Anulado"],
                        required=True
                    ),
                    # ----------------------------------------
                    "Tipo": st.column_config.TextColumn(disabled=True),
                    "Entidad": st.column_config.TextColumn(disabled=True),
                },
                use_container_width=True,
                hide_index=True,
                key="ledger_editor"
            )

            # Persistencia de Cambios
            if not edited_df.equals(df_filtered):
                # Actualizar solo las filas modificadas en el DF maestro
                df_ledger.update(edited_df)
                _save_ledger(df_ledger)
                st.toast("Cambios guardados en Libro Mayor", icon="💾")

    # ---------------------------------------------------------
    # TAB 3: GESTIÓN DE PROVEEDORES (CRUD)
    # ---------------------------------------------------------
    with tab_vendors:
        st.markdown("#### 🏢 Base de Datos de Proveedores & Clientes")

        vendors = _load_vendors()

        # 1. Tabla Resumen
        if vendors:
            st.dataframe(
                pd.DataFrame(vendors).drop(columns=['id']),
                use_container_width=True,
                column_config={
                    "email": st.column_config.LinkColumn("Email", display_text="Enviar Correo")
                }
            )
        else:
            st.info("No hay proveedores registrados. Utilice el formulario de abajo.")

        c_add, c_edit = st.columns(2)

        # 2. Formulario Agregar
        with c_add:
            with st.expander("➕ Agregar Nuevo Proveedor", expanded=False):
                with st.form("add_vendor_form"):
                    v_name = st.text_input("Nombre Empresa *")
                    v_tax = st.text_input("RIF / Fiscal ID")
                    v_contact = st.text_input("Persona Contacto")
                    v_email = st.text_input("Email")
                    v_phone = st.text_input("Teléfono")
                    v_terms = st.selectbox("Términos de Pago", ["Contado", "7 días", "15 días", "30 días", "60 días"])

                    if st.form_submit_button("Guardar Proveedor"):
                        if v_name:
                            new_vendor = {
                                "id": str(uuid.uuid4()),
                                "company_name": v_name,
                                "fiscal_id": v_tax,
                                "contact_name": v_contact,
                                "email": v_email,
                                "phone": v_phone,
                                "payment_terms": v_terms
                            }
                            vendors.append(new_vendor)
                            _save_vendors(vendors)
                            st.success(f"Proveedor '{v_name}' agregado.")
                            st.rerun()
                        else:
                            st.error("El nombre es obligatorio.")

        # 3. Formulario Editar/Eliminar
        with c_edit:
            with st.expander("✏️ Editar / Eliminar Proveedor", expanded=False):
                if not vendors:
                    st.caption("Registre proveedores primero.")
                else:
                    vendor_names = [v['company_name'] for v in vendors]
                    selected_name = st.selectbox("Seleccionar Proveedor", vendor_names)

                    # Encontrar registro seleccionado
                    selected_vendor = next(v for v in vendors if v['company_name'] == selected_name)

                    with st.form("edit_vendor_form"):
                        # Precargar datos
                        new_tax = st.text_input("RIF / Fiscal ID", value=selected_vendor.get('fiscal_id', ''))
                        new_contact = st.text_input("Contacto", value=selected_vendor.get('contact_name', ''))
                        new_phone = st.text_input("Teléfono", value=selected_vendor.get('phone', ''))

                        c_save, c_del = st.columns(2)
                        update = c_save.form_submit_button("Actualizar Datos")
                        delete = c_del.form_submit_button("🗑️ Eliminar", type="primary")

                        if update:
                            selected_vendor['fiscal_id'] = new_tax
                            selected_vendor['contact_name'] = new_contact
                            selected_vendor['phone'] = new_phone
                            _save_vendors(vendors)
                            st.success("Datos actualizados.")
                            st.rerun()

                        if delete:
                            vendors = [v for v in vendors if v['id'] != selected_vendor['id']]
                            _save_vendors(vendors)
                            st.warning(f"Proveedor '{selected_name}' eliminado.")
                            st.rerun()

    # ---------------------------------------------------------
    # TAB 4: REGISTRAR OPERACIÓN (Integrada)
    # ---------------------------------------------------------
    with tab_new:
        st.markdown("#### 🧾 Alta de Operación Financiera")

        vendor_options = _get_vendor_names()

        if not vendor_options:
            st.error("🚫 No hay proveedores registrados.")
            st.markdown("Por favor vaya a la pestaña **'Gestión de Proveedores'** y registre al menos uno para continuar.")
        else:
            with st.form("new_transaction_form", clear_on_submit=True):
                col_a, col_b = st.columns(2)

                with col_a:
                    tipo = st.selectbox("Tipo de Operación", ["Cuentas por Pagar", "Cuentas por Cobrar"])
                    # SELECTOR INTELIGENTE VINCULADO
                    entidad = st.selectbox("Entidad (Proveedor / Cliente)", vendor_options)
                    monto = st.number_input("Monto Total ($)", min_value=0.01, step=10.0)

                with col_b:
                    vencimiento = st.date_input("Fecha Vencimiento", value=date.today() + timedelta(days=30))
                    notas = st.text_area("Notas / Concepto", placeholder="Ej: Factura #1234 - Compra cables")
                    uploaded_file = st.file_uploader("📎 Adjuntar Comprobante", type=['pdf', 'jpg', 'png'])

                submitted = st.form_submit_button("💾 Registrar Operación en Libro Mayor")

                if submitted:
                    # 1. Cargar Ledger Actual
                    df_current = _load_ledger()

                    # 2. Generar ID único
                    new_id = str(uuid.uuid4())

                    # 3. Guardar archivo si existe
                    if uploaded_file:
                        _save_receipt(uploaded_file, new_id)

                    # 4. Crear registro
                    new_row = {
                        "ID": new_id,
                        "Fecha": date.today(),
                        "Tipo": tipo,
                        "Entidad": entidad, # Viene del Selectbox validado
                        "Monto": monto,
                        "Vencimiento": vencimiento,
                        "Notas": notas,
                        "Status": "Pendiente"
                    }

                    # 5. Guardar en CSV
                    df_current = pd.concat([df_current, pd.DataFrame([new_row])], ignore_index=True)
                    _save_ledger(df_current)

                    st.success("✅ Operación registrada exitosamente y vinculada al proveedor.")

