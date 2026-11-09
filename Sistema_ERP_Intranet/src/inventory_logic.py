#!/usr/bin/env python
# coding: utf-8

import pandas as pd
import numpy as np
from typing import Tuple
from src.logger_config import get_custom_logger

# Configuración de Logging Centralizado
inv_logger = get_custom_logger("inventario", "modulo_inventario.log")

def calculate_sales_velocity(df_sales: pd.DataFrame, window_days: int = 120) -> pd.Series:
    """
    Calcula la velocidad de ventas diaria (promedio simple) vectorizado por SKU.
    Basado en el histórico reciente definido por 'window_days'.
    
    Args:
        df_sales (pd.DataFrame): Histórico de ventas limpio ['sku', 'quantity', 'created_at'].
        window_days (int): Días a mirar hacia atrás (Default: 120).

    Returns:
        pd.Series: Serie indexada por SKU con el valor de velocidad diaria.
    """
    if df_sales.empty:
        inv_logger.warning("DataFrame de ventas vacío. Retornando velocidades en 0.")
        return pd.Series(dtype=float)

    try:
        # Asegurar fechas y filtrar periodo de ventana
        cutoff_date = pd.Timestamp.now(tz=df_sales['created_at'].dt.tz) - pd.Timedelta(days=window_days)
        df_filtered = df_sales[df_sales['created_at'] >= cutoff_date]

        # Agrupación vectorizada (mucho más rápido que iterar)
        # Sumamos cantidad total vendida en el periodo y dividimos por los días de la ventana
        total_qty_per_sku = df_filtered.groupby('sku')['quantity'].sum()
        
        # Lógica de negocio: Velocidad = Total Vendido / Días del periodo
        # Usamos fill_value=0 para SKUs sin ventas en el periodo
        velocity = total_qty_per_sku / window_days
        
        inv_logger.info(f"Velocidad calculada para {len(velocity)} SKUs.")
        return velocity.fillna(0.0)

    except Exception as e:
        inv_logger.error(f"Error calculando velocidad de ventas: {e}", exc_info=True)
        return pd.Series(dtype=float)

def calculate_consolidated_stock(
    df_store_stock: pd.DataFrame, 
    df_transit: pd.DataFrame
) -> pd.DataFrame:
    """
    Calcula el Stock Total sumando tiendas físicas y mercancía en tránsito.
    Objetivo de Negocio: Stock Total = Gale + Mille + Tránsito.

    Args:
        df_store_stock: DF con ['sku', 'disponibilidad_galerias', 'disponibilidad_millennium'].
        df_transit: DF con ['sku', 'cantidad_en_transito'].

    Returns:
        pd.DataFrame: DF con columna agregada 'stock_total'.
    """
    try:
        # Merge left para mantener todos los productos del inventario físico
        df_merged = df_store_stock.merge(df_transit, on='sku', how='left').fillna(0)

        # Operación vectorizada de suma
        df_merged['stock_total'] = (
            df_merged.get('disponibilidad_galerias', 0) + 
            df_merged.get('disponibilidad_millennium', 0) + 
            df_merged.get('cantidad_en_transito', 0)
        )
        
        inv_logger.info("Stock total consolidado calculado correctamente.")
        return df_merged[['sku', 'stock_total']]

    except Exception as e:
        inv_logger.error(f"Error consolidando stock: {e}", exc_info=True)
        return pd.DataFrame(columns=['sku', 'stock_total'])

def calculate_reorder_point(
    df_metrics: pd.DataFrame,
    lead_time_days: int,
    security_factor: float = 1.0
) -> pd.DataFrame:
    """
    Calcula Puntos de Reorden con parámetros dinámicos.
    CORRECCIÓN V7: Normaliza la predicción de Prophet a base diaria.
    """
    df = df_metrics.copy()

    # Evitar división por cero
    safe_lead_time = max(1, lead_time_days)

    # 1. Normalizar Prophet a Base Diaria (Fórmula V7)
    # La predicción viene como TOTAL (ej: 120 unidades en 4 meses), la convertimos a diaria.
    df['prophet_daily'] = df['prediccion_prophet'] / safe_lead_time

    # 2. Cálculo de Demanda Promedio (Mean V/P)
    df['mean_demand_daily'] = np.where(
        (df['prophet_daily'] > 0),
        (df['velocidad'] + df['prophet_daily']) / 2,
        df['velocidad']
    )

    # 3. Ajuste de Stock de Seguridad Dinámico
    df['final_safety_stock'] = df['stock_seguridad_base'] * security_factor

    # 4. Fórmula Punto de Reorden
    df['reorder_point'] = (df['mean_demand_daily'] * safe_lead_time) + df['final_safety_stock']

    # 5. Cantidad a Reordenar
    df['qty_to_reorder'] = (df['reorder_point'] - df['stock_total']).clip(lower=0)

    return df

def determine_inventory_status(df_final: pd.DataFrame) -> pd.DataFrame:
    """
    Genera la clasificación semafórica (Estado_Dashboard) para visualización.
    Implementa la lógica estricta solicitada en el prompt.

    Args:
        df_final (pd.DataFrame): DF con ['stock_total', 'reorder_point'].

    Returns:
        pd.DataFrame: DF con nueva columna 'status'.
    """
    # Validar columnas necesarias
    if 'stock_total' not in df_final.columns or 'reorder_point' not in df_final.columns:
        inv_logger.error("Faltan columnas para determinar status de inventario.")
        return df_final

    # Condiciones vectorizadas (np.select es más rápido que apply)
    conditions = [
        (df_final['stock_total'] <= 0),                                     # CRITICO
        (df_final['stock_total'] < df_final['reorder_point']),              # REORDER
        (df_final['stock_total'] > (df_final['reorder_point'] * 3))         # SOBRESTOCK
    ]
    
    choices = ['CRITICO', 'REORDER', 'SOBRESTOCK']
    
    # Si no cumple ninguna anterior, es OPTIMO (Rango medio)
    df_final['status'] = np.select(conditions, choices, default='OPTIMO')
    
    # Logging de resumen para control
    status_counts = df_final['status'].value_counts().to_dict()
    inv_logger.info(f"Clasificación de inventario generada: {status_counts}")
    
    return df_final

