#!/usr/bin/env python
# coding: utf-8

# El orquestador final que une autenticación, carga de datos y las vistas en pestañas.
# 

# In[ ]:


import streamlit as st
from pathlib import Path
import sys

# Asegurar que el directorio raíz está en el path para imports absolutos
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(BASE_DIR))

# Importar módulos locales del dashboard
from src.dashboard.auth import check_password
from src.dashboard.data_loader import load_report_data
from src.dashboard.components.sidebar import render_sidebar
from src.dashboard.components.metrics import render_kpi_row
from src.dashboard.views.operational import render_operational_view
from src.dashboard.views.analytical import render_analytical_view

# Configuración de página (Debe ser la primera llamada de Streamlit)
st.set_page_config(
    page_title="Retail Forecasting V7",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

def main():
    # 1. Seguridad: Verificar Login antes de mostrar nada
    if not check_password():
        return  # Si no está logueado, auth.py detiene la app, pero por seguridad retornamos.

    # 2. Carga de Datos (Cacheada)
    df_raw = load_report_data()
    
    if df_raw.empty:
        st.error("No se pudieron cargar los datos. Verifique que el backend haya generado 'reporte_final.csv'.")
        return

    # 3. Sidebar y Filtros
    filters = render_sidebar(df_raw)
    
    # 4. Aplicar Filtros al DataFrame
    df_filtered = df_raw.copy()
    
    # Filtro de Estado
    if filters["status"]:
        df_filtered = df_filtered[df_filtered['status'].isin(filters["status"])]
        
    # Filtro de Búsqueda (Nombre o SKU)
    if filters["search"]:
        term = filters["search"].lower()
        df_filtered = df_filtered[
            df_filtered['nombre'].astype(str).str.lower().str.contains(term) | 
            df_filtered['sku'].astype(str).str.lower().str.contains(term)
        ]
        
    # Filtro de Tránsito
    if filters["transit"]:
        df_filtered = df_filtered[df_filtered['cantidad_en_transito'] > 0]

    # 5. Renderizado del Área Principal
    st.title("Tablero de Control de Inventario 🚀")
    
    # Métricas Globales (Calculadas sobre la selección filtrada)
    render_kpi_row(df_filtered)

    # Pestañas
    tab_op, tab_an = st.tabs(["📋 Operativo (Compras)", "📊 Estratégico (IA)"])
    
    with tab_op:
        render_operational_view(df_filtered)
        
    with tab_an:
        render_analytical_view(df_filtered)

if __name__ == "__main__":
    main()

