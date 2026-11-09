#!/usr/bin/env python
# coding: utf-8

# In[ ]:


import pandas as pd
import logging
from typing import Dict, List

logger = logging.getLogger("data_processing")

def _is_valid_match_vectorized(hist_names: pd.Series, current_name: str) -> pd.Series:
    """
    Versión vectorizada de la lógica difusa.
    Compara una serie de nombres históricos contra UN nombre actual.
    """
    if not current_name:
        return pd.Series([False] * len(hist_names), index=hist_names.index)

    # Normalización rápida
    hist_upper = hist_names.str.upper().str.strip()
    curr_upper = current_name.upper().strip()
    curr_tokens = curr_upper.split()
    
    if not curr_tokens:
        return pd.Series([False] * len(hist_names), index=hist_names.index)

    marca = curr_tokens[0]
    modelo = curr_tokens[1] if len(curr_tokens) > 1 else ""

    # Regla 1: Coincidencia Exacta
    mask_exact = (hist_upper == curr_upper)
    
    # Regla 2: Marca Y Modelo están contenidos (si modelo existe)
    if modelo:
        mask_fuzzy = (hist_upper.str.contains(marca, regex=False)) & \
                     (hist_upper.str.contains(modelo, regex=False))
    else:
        # Si no hay modelo, solo marca (riesgoso, pero válido si es genérico)
        mask_fuzzy = hist_upper.str.contains(marca, regex=False)

    return mask_exact | mask_fuzzy

def clean_sku_history(df_history: pd.DataFrame, current_catalog: Dict[str, str]) -> pd.DataFrame:
    """
    Depura el histórico filtrando SKUs con menos de 3 meses (90 días) de antigüedad.
    Se elimina la validación por nombre para evitar pérdidas de predicción al cambiar títulos.
    """
    if df_history.empty:
        return pd.DataFrame()

    logger.info("Iniciando depuración de SKUs por regla de antigüedad (90 días)...")
    
    # 1. Filtrar solo SKUs que existen hoy en el catálogo
    valid_skus = set(current_catalog.keys())
    df_filtered = df_history[df_history['sku'].astype(str).isin(valid_skus)].copy()
    
    if df_filtered.empty:
        return pd.DataFrame()

    # 2. Asegurar formato de fecha en UTC para evitar errores de zona horaria
    df_filtered['created_at'] = pd.to_datetime(df_filtered['created_at'], utc=True)
    
    # 3. Calcular la fecha de la primera venta por SKU
    antiguedad_sku = df_filtered.groupby('sku')['created_at'].min().reset_index()
    
    # 4. Filtrar SKUs que tengan su primera venta hace más de 90 días
    fecha_limite = pd.Timestamp.now(tz='UTC') - pd.Timedelta(days=90)
    skus_validos = antiguedad_sku[antiguedad_sku['created_at'] <= fecha_limite]['sku']
    
    # 5. Aplicar el filtro final al dataset
    df_clean = df_filtered[df_filtered['sku'].isin(skus_validos)].copy()
    
    # 6. Actualizar el nombre histórico con el nombre fresco del catálogo actual
    df_clean['item_name'] = df_clean['sku'].astype(str).map(current_catalog).fillna(df_clean['item_name'])
    
    dropped_count = len(df_history) - len(df_clean)
    logger.info(f"Limpieza finalizada. Mantenidos: {len(df_clean)} registros. Eliminados: {dropped_count}")
    
    return df_clean

def impute_missing_dates(df_sales: pd.DataFrame) -> pd.DataFrame:
    """Rellena días sin ventas con 0 (Sin cambios, ya era eficiente)."""
    if df_sales.empty:
        return pd.DataFrame()

    try:
        df_sales['created_at'] = pd.to_datetime(df_sales['created_at']).dt.normalize()
        # Agrupación inicial
        daily = df_sales.groupby(['sku', 'created_at'])['quantity'].sum().reset_index()
        
        # Pivoteo masivo (Truco de velocidad para no iterar SKUs)
        # Crea una matriz Fecha x SKU llena de ceros
        pivot = daily.pivot(index='created_at', columns='sku', values='quantity').fillna(0)
        
        # Reindexar para asegurar rango completo de fechas global
        full_idx = pd.date_range(start=daily['created_at'].min(), end=daily['created_at'].max(), freq='D')
        pivot = pivot.reindex(full_idx, fill_value=0)
        
        # Deshacer pivote (Melt) para volver a formato largo
        df_final = pivot.reset_index().melt(id_vars='index', var_name='sku', value_name='quantity')
        df_final.rename(columns={'index': 'created_at'}, inplace=True)
        
        return df_final
    except Exception as e:
        logger.error(f"Error imputando fechas: {e}")
        return df_sales

