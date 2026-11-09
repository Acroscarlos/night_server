#!/usr/bin/env python
# coding: utf-8

import streamlit as st
from pathlib import Path
import sys
import io
import contextlib
import json

# 1. Configuración de página (SIEMPRE PRIMERO)
st.set_page_config(
    page_title="Retail ERP & Forecasting V8",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 2. Configuración de Rutas 
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Rutas para el módulo Admin
OUTPUT_DIR = BASE_DIR / "output"
CONFIG_FILE = BASE_DIR / "config" / "config.json"

# 3. Importación del Logger 
from src.logger_config import get_custom_logger
ui_logger = get_custom_logger("dashboard", "dashboard_ui.log")

# Importar módulos locales
from src.dashboard.auth import check_password
from src.dashboard.data_loader import load_report_data
from src.dashboard.components.sidebar import render_sidebar
from src.dashboard.components.metrics import render_kpi_row

# Vistas Existentes (Views)
from src.dashboard.views.operational import render_operational_view
from src.dashboard.views.analytical import render_analytical_view
from src.dashboard.views.finance import render_finance_view
from src.dashboard.views.compliance import render_compliance_view
from src.dashboard.views.projects import render_projects_view
from src.dashboard.views.allies import render_allies_view
from src.dashboard.views.pricing import render_pricing_view
from src.dashboard.views.purchase_orders import render_purchase_orders_view
from src.dashboard.views.movements import render_movements_view

# ==============================================================================
# VISTA EXCLUSIVA DE ADMINISTRACIÓN (SOLO ADMINS)
# ==============================================================================
def render_admin_omnicanal_tab(usuario_actual):
    """Renderiza la pestaña de control de precios exclusiva para el Admin."""
    if usuario_actual not in ["admin", "acroscarlos"]:
        st.error("⛔ Acceso denegado. No tienes permisos de Administrador Omnicanal.")
        st.image("https://http.cat/403", width=400)
        ui_logger.warning(f"Intento de acceso denegado a la pestaña Admin por el usuario: {usuario_actual}")
        return

    st.header("🛠️ Panel de Control: Precios")
    st.markdown("Desde aquí puedes **forzar la actualización manual de precios** leyendo los CSV de la carpeta `output/`.")

    col1, col2 = st.columns(2)
    log_capture_string = io.StringIO()

    with col1:
        st.subheader("🛒 WooCommerce (Precios)")
        if st.button("🚀 Forzar Precios WooCommerce", width="stretch"):
            ui_logger.info(f"El administrador '{usuario_actual}' forzó la actualización de precios de WooCommerce.")
            with st.spinner("Sincronizando precios con WooCommerce..."):
                try:
                    with open(CONFIG_FILE, 'r') as f: config = json.load(f)
                    from src.inventario_Auto.precios_woo import ejecutar_precios_woo
                    with contextlib.redirect_stdout(log_capture_string):
                        ruta_csv = str(OUTPUT_DIR / "matrix_de_precios.csv") # NOMBRE DEL ARCHIVO QUE CONTIENE LOS PRECIOS
                        ejecutar_precios_woo(config, ruta_csv)
                    st.success("✅ Actualización de WooCommerce finalizada.")
                except Exception as e:
                    st.error(f"❌ Error crítico: {str(e)}")
                    ui_logger.error(f"Error forzando Woo desde UI: {e}", exc_info=True)

    with col2:
        st.subheader("🛍️ Cashea (Precios)")
        if st.button("🚀 Forzar Precios Cashea", width="stretch"):
            ui_logger.info(f"El administrador '{usuario_actual}' forzó la actualización de precios de Cashea.")
            with st.spinner("Sincronizando precios con Cashea..."):
                try:
                    with open(CONFIG_FILE, 'r') as f: config = json.load(f)
                    from src.inventario_Auto.precios_cashea import ejecutar_precios_cashea
                    with contextlib.redirect_stdout(log_capture_string):
                        ruta_csv = str(OUTPUT_DIR / "matrix_de_precios.csv") # NOMBRE DEL ARCHIVO QUE CONTIENE LOS PRECIOS
                        ejecutar_precios_cashea(config, ruta_csv)
                    st.success("✅ Actualización de Cashea finalizada.")
                except Exception as e:
                    st.error(f"❌ Error crítico: {str(e)}")
                    ui_logger.error(f"Error forzando Cashea desde UI: {e}", exc_info=True)


    # ==========================================
    # 🔐 MÓDULO DE SEGURIDAD: RESETEO POR LLAVE MAESTRA
    # ==========================================
    st.divider()
    st.subheader("🔐 Gestión de Accesos (Auditoría SecOps)")
    
    # Leemos la lista de usuarios para el menú desplegable
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config_seguridad = json.load(f)
        usuarios_lista = list(config_seguridad.get("dashboard", {}).get("usuarios_permitidos", {}).keys())
    except:
        usuarios_lista = []

    with st.form("reset_password_form", border=True):
        st.markdown("Usa tu credencial de Administrador como **Llave Maestra** para cambiar la clave de cualquier usuario.")
        
        col_target, col_admin = st.columns(2)
        
        with col_target:
            st.caption("👤 Datos del Usuario a Modificar")
            target_user = st.selectbox("Seleccionar Usuario", options=usuarios_lista)
            new_password = st.text_input("Nueva Contraseña", type="password")
            confirm_password = st.text_input("Confirmar Nueva Contraseña", type="password")
            
        with col_admin:
            st.caption("🛡️ Autorización de Seguridad")
            st.info(f"Autorizando modificación como: **{usuario_actual}**")
            admin_password = st.text_input("Tu Contraseña de Administrador (Llave Maestra)", type="password")
            
        submit_reset = st.form_submit_button("🔄 Restablecer Contraseña", type="primary", use_container_width=True)
        
        if submit_reset:
            # Validaciones de Front-End
            if new_password != confirm_password:
                st.error("❌ Las contraseñas nuevas no coinciden.")
            elif len(new_password) < 6:
                st.error("❌ La nueva contraseña debe tener al menos 6 caracteres por seguridad.")
            elif not admin_password:
                st.error("❌ Debes ingresar tu Llave Maestra para autorizar la transacción.")
            else:
                # Ejecución de Back-End
                from src.dashboard.auth import admin_reset_password
                exito, mensaje = admin_reset_password(
                    admin_username=usuario_actual,
                    admin_password=admin_password,
                    target_user=target_user,
                    new_password=new_password,
                    config_path=CONFIG_FILE
                )
                
                if exito:
                    st.success(f"✅ {mensaje}")
                    ui_logger.info(f"AUDITORÍA: El administrador '{usuario_actual}' reseteó la contraseña del usuario '{target_user}'.")
                else:
                    st.error(f"⛔ {mensaje}")
                    ui_logger.warning(f"AUDITORÍA: Intento fallido de reseteo de contraseña por '{usuario_actual}': {mensaje}")
                    
    st.divider()
    st.subheader("Terminal de Ejecución")
    logs = log_capture_string.getvalue()
    if logs: st.code(logs, language="bash")
    else: st.caption("Los registros de la API aparecerán aquí cuando ejecutes una sincronización.")


def inyectar_estilos_enterprise():
    st.markdown("""
    <style>
        /* Tipografía moderna y limpia */
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
        html, body, [class*="css"] {
            font-family: 'Inter', sans-serif;
        }
        /* Botones de acción principales (Estilo Enterprise) */
        button[kind="primary"] {
            background-color: #1E3A8A !important;
            color: white !important;
            border-radius: 8px !important;
            border: none !important;
            box-shadow: 0 4px 6px rgba(30, 58, 138, 0.15) !important;
            transition: all 0.3s ease !important;
            font-weight: 500 !important;
        }
        button[kind="primary"]:hover {
            transform: translateY(-2px) !important;
            box-shadow: 0 6px 12px rgba(30, 58, 138, 0.25) !important;
        }
        /* Refinamiento de DataFrames y bordes */
        [data-testid="stDataFrame"] {
            border-radius: 10px;
            overflow: hidden;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }
    </style>
    """, unsafe_allow_html=True)
# ==============================================================================
# ORQUESTADOR PRINCIPAL Y NAVEGACIÓN
# ==============================================================================
def main():
    
    inyectar_estilos_enterprise() # Aseguramos que los estilos carguen siempre

    # ==========================================
    # 🚀 NUEVO: ACCESO PÚBLICO A LA PANTALLA TV
    # ==========================================
    if "modo_tv" not in st.session_state:
        st.session_state.modo_tv = False

    if st.session_state.modo_tv:
        # Importamos la vista dinámica de la TV
        from src.dashboard.views.noc_wallboard import render_live_wallboard
        
        # Botón discreto arriba a la izquierda por si necesitas salir de la pantalla
        if st.button("⬅️ Salir de Pantalla TV"):
            st.session_state.modo_tv = False
            st.rerun()
            
        render_live_wallboard()
        return  # 🛑 DETIENE LA EJECUCIÓN AQUÍ. No pide password ni carga el ERP.

    # --- BOTÓN EN LA PANTALLA DE LOGIN ---
    # Colocamos el botón público alineado a la derecha antes del login
    col_space, col_btn = st.columns([4, 1])
    with col_btn:
        if st.button("📺 Información para Pantalla", use_container_width=True):
            st.session_state.modo_tv = True
            st.rerun()


    # 1. Seguridad
    if not check_password():
        return
    inyectar_estilos_enterprise()
    usuario_actual = st.session_state.get("username", "Desconocido")
    es_admin = usuario_actual in ["admin", "acroscarlos"]

    # Log de inicio de sesión (1 sola vez)
    if "session_logged" not in st.session_state:
        ui_logger.info(f"El usuario '{usuario_actual}' ha iniciado sesión exitosamente en el ERP.")
        st.session_state.session_logged = True

    # ==============================================================================
    # 🛡️ INTERCEPTOR DE ROL VENDEDOR (JAULA EXCLUSIVA: app_planillas_ecommerce)
    # ==============================================================================
    from src.dashboard.data_loader import load_config
    from src.dashboard.views.planillas_ecommerce import app_planillas_ecommerce

    config_actual = load_config()
    # Puedes controlar quién entra en modo exclusivo desde config.json en "usuarios_solo_planillas"
    # Por defecto, cualquier usuario que NO sea admin o esté en esa lista irá directo a la jaula:
    usuarios_restringidos = config_actual.get("dashboard", {}).get("usuarios_solo_planillas", [])
    es_vendedor_exclusivo = usuario_actual in usuarios_restringidos

    if es_vendedor_exclusivo:
        with st.sidebar:
            st.markdown("### ⚙️ Portal de Vendedores")
            st.markdown(f"👤 **Usuario:** `{usuario_actual}`")
            st.caption("Rol: Gestión de Planillas E-Commerce")
            st.divider()
            if st.button(f"🚪 Cerrar Sesión ({usuario_actual})", use_container_width=True):
                st.session_state["force_logout"] = True
                st.rerun()

        # Renderizamos ÚNICAMENTE la función nueva y detenemos la ejecución del resto del ERP
        app_planillas_ecommerce()
        return  # 🛑 CORTAFUEGOS: Impide que el vendedor vea KPIs, Finanzas o Cadena de Suministro

    # 2. Preferencias Visuales (Sidebar Superior para Admins)
    with st.sidebar:
        st.markdown("### ⚙️ Panel de Usuario")
        st.markdown(f"👤 **Activo:** `{usuario_actual}`")
        st.divider()

    # 3. Carga de Datos y Filtros
    df_raw = load_report_data()
    filters = render_sidebar(df_raw)
    
    df_filtered = df_raw.copy()
    if not df_filtered.empty:
        if filters["status"]:
            df_filtered = df_filtered[df_filtered['status'].isin(filters["status"])]
        if filters["search"]:
            term = filters["search"].lower()
            df_filtered = df_filtered[
                df_filtered['nombre'].astype(str).str.lower().str.contains(term) | 
                df_filtered['sku'].astype(str).str.lower().str.contains(term)
            ]
        if filters["transit"]:
            df_filtered = df_filtered[df_filtered['cantidad_en_transito'] > 0]

    # 4. Header y KPIs Boxed
    st.title("🏢 Retail ERP & Forecasting V8")
    
    if not df_filtered.empty:
        with st.container(border=True):
            render_kpi_row(df_filtered)

    st.markdown("<br>", unsafe_allow_html=True)

    # ==============================================================================
    # 5. ÁRBOL DE NAVEGACIÓN (SISTEMA DE PILLS ENTERPRISE)
    # ==============================================================================
    modulos_base = [
        "📦 Cadena de Suministro", 
        "💰 Libro Mayor y Finanzas", 
        "⚖️ Legal y Cumplimiento", 
        "🤝 Aliados Comerciales",
        "🚀 Gestor de Proyectos", 
        "📈 Business Intelligence (BI)"
    ]

    # Ocultamiento de pestaña Controladores
    if es_admin:
        modulos_base.append("⚙️ Controladores")

    # --- Nivel 1: Navegación Principal ---
    modulo_seleccionado = st.pills(
        "Navegación Principal", 
        options=modulos_base, 
        default="📦 Cadena de Suministro", 
        label_visibility="collapsed"
    )

    st.divider()

    # --- Nivel 2: Sub-navegación y Renderizado de Vistas ---
    
    # 1) CADENA DE SUMINISTRO
    if modulo_seleccionado == "📦 Cadena de Suministro":
        sub_supply = [
            "📋 Planilla de Reabastecimiento", 
            "🛒 Órdenes de Compra", 
            "🏷️ Inteligencia de Precios", 
            "🚚 Movimientos y Entregas",
            "🛍️ Planillas E-Commerce"
        ]
        vista_seleccionada = st.pills(
            "Sub-Navegación Supply", 
            options=sub_supply, 
            default="📋 Planilla de Reabastecimiento", 
            label_visibility="collapsed"
        )
        
        if vista_seleccionada == "📋 Planilla de Reabastecimiento":
            if not df_filtered.empty: render_operational_view(df_filtered)
            else: st.warning("Cargue datos de inventario para ver esta sección.")
        elif vista_seleccionada == "🛒 Órdenes de Compra":
            render_purchase_orders_view()
        elif vista_seleccionada == "🏷️ Inteligencia de Precios":
            render_pricing_view()
        elif vista_seleccionada == "🚚 Movimientos y Entregas":
            render_movements_view()
        elif vista_seleccionada == "🛍️ Planillas E-Commerce":
            app_planillas_ecommerce()

    # 2) MÓDULOS CORPORATIVOS APLANADOS
    elif modulo_seleccionado == "💰 Libro Mayor y Finanzas": 
        render_finance_view()
    
    elif modulo_seleccionado == "⚖️ Legal y Cumplimiento": 
        render_compliance_view()
    
    elif modulo_seleccionado == "🤝 Aliados Comerciales": 
        render_allies_view()

    # 3) PROYECTOS KANBAN
    elif modulo_seleccionado == "🚀 Gestor de Proyectos":
        render_projects_view()

    # 4) INTELIGENCIA ARTIFICIAL
    elif modulo_seleccionado == "📈 Business Intelligence (BI)":
        with st.expander("ℹ️ ¿Cómo leer este panel?", expanded=False):
            st.info("Visualiza las predicciones de demanda a 30 días.")
        if not df_filtered.empty: render_analytical_view(df_filtered)
        else: st.warning("Cargue datos para visualizar predicciones.")
        

    
    # 5) CONTROLADORES (ADMIN)
    elif modulo_seleccionado == "⚙️ Controladores" and es_admin:
        sub_ctrl = ["🛠️ Precios Woo y Cashea", "🏪 Catálogos Maestros"]
        vista_seleccionada = st.pills(
            "Sub-Navegación Admin", 
            options=sub_ctrl, 
            default="🛠️ Precios Woo y Cashea", 
            label_visibility="collapsed"
        )
        
        if vista_seleccionada == "🛠️ Precios Woo y Cashea":
            with st.container(border=True):
                # Tu lógica original intacta
                render_admin_omnicanal_tab(usuario_actual)
                
        elif vista_seleccionada == "🏪 Catálogos Maestros":
            with st.container(border=True):
                # Importación dinámica para evitar errores circulares
                from src.dashboard.views.admin_controllers import render_catalogos_maestros_view
                render_catalogos_maestros_view()

if __name__ == "__main__":
    main()