#!/usr/bin/env python
# coding: utf-8

# Este módulo maneja la carga eficiente de datos y configuración, vital para el rendimiento de Streamlit. Implementa caché y manejo de errores si falta el CSV.
# 

# In[ ]:


import json
import pandas as pd
import streamlit as st
from pathlib import Path
from typing import Dict, Optional

# Definir rutas relativas base
BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = BASE_DIR / "config" / "config.json"
DATA_PATH = BASE_DIR / "output" / "reporte_final.csv"

def load_config() -> Dict:
    """
    Carga la configuración desde el archivo JSON central.
    Retorna un diccionario vacío si falla para no romper la app.
    """
    if not CONFIG_PATH.exists():
        st.error(f"Error Crítico: No se encontró el archivo de configuración en {CONFIG_PATH}")
        return {}
    
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        st.error(f"Error leyendo config.json: {e}")
        return {}

@st.cache_data(ttl=3600)  # Caché por 1 hora o hasta que cambie el archivo
def load_report_data() -> pd.DataFrame:
    """
    Carga el reporte final CSV generado por el backend.
    Usa encoding utf-8-sig para compatibilidad con Excel/Windows.
    """
    if not DATA_PATH.exists():
        st.warning("⚠️ El archivo 'reporte_final.csv' no existe todavía. Ejecute el pipeline de backend.")
        # Retornar DataFrame vacío con columnas esperadas para evitar crash en UI
        return pd.DataFrame(columns=[
            'sku', 'nombre', 'status', 'stock_total', 'reorder_point', 
            'qty_to_reorder', 'velocidad', 'prediccion_prophet', 
            'mean_demand', 'cantidad_en_transito'
        ])

    try:
        df = pd.read_csv(DATA_PATH, encoding='utf-8-sig')
        
        # Asegurar tipos de datos críticos para filtrado y visualización
        cols_numericas = ['stock_total', 'reorder_point', 'qty_to_reorder', 
                          'velocidad', 'prediccion_prophet', 'cantidad_en_transito']
        
        for col in cols_numericas:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
                
        return df
    except Exception as e:
        st.error(f"Error cargando datos: {e}")
        return pd.DataFrame()

