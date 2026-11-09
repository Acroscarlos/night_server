#!/usr/bin/env python
# -*- coding: utf-8 -*-

import streamlit as st
import pandas as pd
from typing import Dict, Any

def render_sidebar(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Renderiza la barra lateral con filtros globales.
    Retorna un diccionario con los valores seleccionados.
    """
    st.sidebar.markdown("## 🎛️ Panel de Control")
    st.sidebar.divider()
    
    # Filtro 1: Buscador de Texto (Simplificado y limpio)
    st.sidebar.markdown("**🔍 Búsqueda Rápida**")
    search_term = st.sidebar.text_input("Buscar SKU o Nombre", "", placeholder="Ej. UNI-T...", label_visibility="collapsed")

    st.sidebar.divider()

    # Filtro 2 y 3: Agrupados en Popover Flotante
    with st.sidebar.popover("⚙️ Filtros Avanzados", width="stretch"):
        st.markdown("#### Configuración de Vista")
        
        # Extraer estados únicos del DF si existen, sino usar defaults
        if 'status' in df.columns:
            unique_statuses = sorted(df['status'].dropna().astype(str).unique().tolist())
        else:
            unique_statuses = ['CRITICO', 'REORDER', 'OPTIMO', 'SOBRESTOCK']
            
        default_opts = [s for s in unique_statuses if s in ['CRITICO', 'REORDER']]
        
        # Selección moderna: Botones tipo Píldora
        selected_status = st.pills(
            "Estado del Inventario",
            options=unique_statuses,
            default=default_opts,
            selection_mode="multi"
        )
        
        st.divider()
        # Switch nativo y moderno
        only_transit = st.toggle("Mostrar solo mercancía en tránsito", value=False)

    st.sidebar.markdown("---")
    st.sidebar.markdown("**Información del Sistema**")
    st.sidebar.caption(f"Total SKUs cargados: {len(df)}")
    st.sidebar.caption("Versión: V8.0 (Catálogos Dinámicos)")

    # ==========================================
    # BOTÓN DE CIERRE DE SESIÓN (PATRÓN NATIVO)
    # ==========================================
    # Usamos saltos de línea HTML para empujar el botón al fondo de la barra
    st.sidebar.markdown("<br><br>", unsafe_allow_html=True)
    
    usuario_actual = st.session_state.get("username", "Usuario")
    
    if st.sidebar.button(f"🚪 Cerrar Sesión ({usuario_actual})", use_container_width=True):
        # 1. Levantamos la bandera de destrucción global
        st.session_state["force_logout"] = True
        # 2. Rerun limpio para reiniciar el orquestador
        st.rerun()

    return {
        "search": search_term,
        "status": selected_status,
        "transit": only_transit
    }