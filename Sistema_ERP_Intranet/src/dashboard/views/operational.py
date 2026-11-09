#!/usr/bin/env python
# coding: utf-8


import streamlit as st
import pandas as pd

def render_operational_view(df: pd.DataFrame):
    """
    Muestra la tabla operativa principal enfocada en la decisión de compra.
    """
    st.markdown("### 📋 Planilla de Reabastecimiento")
    st.caption("Visualiza tu inventario en tiempo real y toma decisiones de compra guiadas por Inteligencia Artificial.")
    st.divider()
    
    if df.empty:
        st.info("No hay productos que coincidan con los filtros seleccionados. Intenta ampliar tu búsqueda.", icon="ℹ️")
        return

    # Selección de columnas clave para el operador
    cols_to_show = [
        'sku', 'nombre', 'status', 'stock_total', 
        'reorder_point', 'qty_to_reorder', 'cantidad_en_transito', 
        'velocidad', 'prediccion_prophet'
    ]
    
    # Filtrar columnas que realmente existen en el DF para evitar errores (Failsafe)
    final_cols = [c for c in cols_to_show if c in df.columns]
    df_view = df[final_cols].copy()

    # ==========================================
    # SOLUCIÓN UX - TRADUCCIÓN DE JERGA EN TABLAS
    # ==========================================
    with st.container(border=True):
        st.dataframe(
            df_view,
            column_config={
                "sku": st.column_config.TextColumn(
                    "SKU", 
                    width="small"
                ),
                "nombre": st.column_config.TextColumn(
                    "Producto", 
                    width="large"
                ),
                "status": st.column_config.TextColumn(
                    "Estado 🚦",
                    help="Salud actual del inventario (ej. Normal, Crítico, Exceso).",
                    width="medium"
                ),
                "stock_total": st.column_config.ProgressColumn(
                    "Stock Disponible",
                    help="Mercancía física en el almacén + Mercancía en camino.",
                    format="%d u", 
                    min_value=0,
                    # Seguro anti-crashes si el max() es NaN o 0
                    max_value=int(df['stock_total'].max()) if not df.empty and pd.notna(df['stock_total'].max()) else 100,
                ),
                "reorder_point": st.column_config.NumberColumn(
                    "Punto de Reorden",
                    help="Nivel de alerta. Si tu stock cae por debajo de este número, te quedarás sin mercancía pronto.",
                    format="%d u"
                ),
                "qty_to_reorder": st.column_config.NumberColumn(
                    "A COMPRAR HOY",
                    format="%d u",
                    help="La cantidad exacta que debes pedir al proveedor HOY para evitar romper stock.",
                ),
                "cantidad_en_transito": st.column_config.NumberColumn(
                    "Stock en Camino",
                    help="Unidades que ya compraste al proveedor y están viajando hacia el almacén.",
                    format="%d u"
                ),
                "velocidad": st.column_config.NumberColumn(
                    "Ritmo de Ventas",
                    help="Promedio de cuántas unidades se están vendiendo diariamente.",
                    format="%.2f u/día" 
                ),
                "prediccion_prophet": st.column_config.NumberColumn(
                    "Sugerencia de IA", 
                    help="Cantidad que nuestra Inteligencia Artificial sugiere comprar basada en el histórico, tendencias y estacionalidad.",
                    format="%d u"
                )
            },
            width='stretch',
            hide_index=True,
            height=600
        )

