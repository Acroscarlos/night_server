#!/usr/bin/env python
# coding: utf-8

# Este módulo implementa la arquitectura de Torneo de Modelos (Competitive Forecasting), enfrentando a Prophet contra XGBoost, con ingeniería de características avanzada y selección automática del ganador basada en métricas de error (MAE).
# 

# In[ ]:


import os
import json
import logging
import numpy as np
import pandas as pd
from prophet import Prophet
from xgboost import XGBRegressor
from sklearn.metrics import mean_absolute_error
from datetime import timedelta
from typing import Dict, List, Optional, Any, Tuple

# --- CONFIGURACIÓN ---
logging.getLogger('cmdstanpy').setLevel(logging.WARNING)
logging.getLogger('prophet').setLevel(logging.WARNING)
logging.getLogger('xgboost').setLevel(logging.WARNING)

logger = logging.getLogger("forecasting_engine")

# Constantes V7
MIN_HISTORY_FOR_XGB = 60  # XGBoost necesita más datos que Prophet para aprender patrones

# --- FUNCIONES AUXILIARES ---

def configure_holidays(year_range: List[int]) -> pd.DataFrame:
    """Genera feriados para Prophet."""
    holidays_data = []
    # Lista simplificada de feriados fijos
    fixed_dates = [
        (1, 1, 'Año Nuevo', 'holiday_closed'),
        (4, 19, 'Independencia', 'holiday_promo'),
        (5, 1, 'Trabajador', 'holiday_closed'),
        (6, 24, 'Carabobo', 'holiday_promo'),
        (7, 5, 'Independencia', 'holiday_closed'),
        (12, 24, 'Navidad', 'holiday_promo'),
        (12, 25, 'Navidad_Cierre', 'holiday_closed'),
        (12, 31, 'Fin de Año', 'holiday_promo')
    ]
    for year in year_range:
        for m, d, name, cat in fixed_dates:
            try:
                holidays_data.append({
                    'holiday': cat,
                    'ds': pd.to_datetime(f"{year}-{m:02d}-{d:02d}"),
                    'lower_window': -2 if 'promo' in cat else 0,
                    'upper_window': 1
                })
            except: continue
    return pd.DataFrame(holidays_data)

def get_optimal_params(sku: str, cache_path: str) -> Dict[str, Any]:
    """Carga parámetros (Compatibilidad V6)."""
    default = {'growth': 'linear', 'seasonality_mode': 'additive', 'changepoint_prior_scale': 0.05}
    if not os.path.exists(cache_path): return default
    try:
        with open(cache_path, 'r') as f:
            return json.load(f).get('parametros', {}).get(str(sku), default)
    except: return default

# --- INGENIERÍA DE CARACTERÍSTICAS XGBOOST ---

def _create_xgb_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Crea features para XGBoost. 
    CORRECCIÓN: Lags explícitos y seguros para evitar Data Leakage.
    """
    df = df.copy().sort_values('ds')
    
    # Fecha
    df['day_of_week'] = df['ds'].dt.dayofweek
    df['month'] = df['ds'].dt.month
    df['day_of_year'] = df['ds'].dt.dayofyear
    
    # Lags (Ventas pasadas)
    # Importante: Lag 1 es "ayer", Lag 7 es "hace una semana"
    # CORRECCIÓN: Usamos una lista explícita, no [1-3]
    for lag in [1, 7, 14, 28]: 
        df[f'lag_{lag}'] = df['y'].shift(lag)
        
    # Medias móviles (Rolling) - Shift 1 para asegurar que usamos datos pasados
    df['rolling_mean_7'] = df['y'].shift(1).rolling(window=7).mean()
    df['rolling_mean_30'] = df['y'].shift(1).rolling(window=30).mean()
    
    return df

def _predict_xgb_recursive(model: XGBRegressor, df_history: pd.DataFrame, horizon: int) -> float:
    """
    Predicción paso a paso (recursiva) para poder usar lags en el futuro.
    """
    future_df = df_history.copy()
    last_date = future_df['ds'].max()
    
    for i in range(1, horizon + 1):
        next_date = last_date + timedelta(days=i)
        
        # 1. Agregar fila vacía
        new_row = pd.DataFrame({'ds': [next_date], 'y': [np.nan]})
        future_df = pd.concat([future_df, new_row], ignore_index=True)
        
        # 2. Recalcular features (rellena los lags usando las predicciones previas)
        # Nota: Esto es costoso computacionalmente pero necesario para exactitud
        df_feats = _create_xgb_features(future_df)
        
        # 3. Predecir solo el día actual
        feat_cols = [c for c in df_feats.columns if c not in ['ds', 'y']]
        current_feat = df_feats.iloc[[-1]][feat_cols]
        
        pred = model.predict(current_feat)[0]
        pred = max(0.0, pred) # ReLU
        
        # 4. Guardar predicción para que sirva de lag al día siguiente
        future_df.iloc[-1, future_df.columns.get_loc('y')] = pred
        
    # Sumar solo el horizonte predicho
    return float(future_df.iloc[-horizon:]['y'].sum())

# --- MOTORES DE ENTRENAMIENTO ---

def _train_prophet(df: pd.DataFrame, horizon: int, params: Dict) -> float:
    try:
        years = df['ds'].dt.year.unique().tolist()
        years.append(max(years) + 1)
        holidays = configure_holidays(years)
        
        m = Prophet(holidays=holidays, **params)
        m.fit(df)
        future = m.make_future_dataframe(periods=horizon)
        fcst = m.predict(future)
        return float(fcst.iloc[-horizon:]['yhat'].clip(lower=0).sum())
    except:
        return 0.0

def _evaluate_champion(df: pd.DataFrame, horizon: int, params: Dict) -> str:
    """Backtesting: Retorna 'prophet' o 'xgboost'."""
    # Si hay pocos datos, XGBoost no aprende bien -> Gana Prophet por default
    if len(df) < MIN_HISTORY_FOR_XGB:
        return 'prophet'

    # Split
    cutoff = df['ds'].max() - timedelta(days=14) # Validamos con los últimos 14 días
    train = df[df['ds'] <= cutoff].copy()
    test = df[df['ds'] > cutoff].copy()
    
    if test.empty: return 'prophet'

    # 1. Error Prophet
    try:
        years = train['ds'].dt.year.unique()
        hols = configure_holidays(years)
        m = Prophet(holidays=hols, **params).fit(train)
        future = m.make_future_dataframe(periods=len(test))
        # Ajustamos el índice para comparar día a día
        fcst = m.predict(future).iloc[-len(test):]['yhat'].clip(lower=0).values
        mae_p = mean_absolute_error(test['y'].values, fcst)
    except: mae_p = float('inf')

    # 2. Error XGBoost
    try:
        df_feat = _create_xgb_features(train).dropna()
        feat_cols = [c for c in df_feat.columns if c not in ['ds', 'y']]
        # CORRECCIÓN: n_jobs=1 para no chocar con el paralelismo de main.py
        model = XGBRegressor(n_estimators=100, objective='reg:absoluteerror', n_jobs=1)
        model.fit(df_feat[feat_cols], df_feat['y'])
        
        # Simulación recursiva en test
        preds_x = []
        hist_sim = train.copy()
        for i in range(len(test)):
            p = _predict_xgb_recursive(model, hist_sim, 1)
            preds_x.append(p)
            # Truco: En backtest real, usamos el dato REAL para el lag siguiente (Teacher Forcing)
            # para evaluar la capacidad de un paso adelante.
            real_next = test.iloc[[i]]
            hist_sim = pd.concat([hist_sim, real_next], ignore_index=True)
            
        mae_x = mean_absolute_error(test['y'].values, preds_x)
    except: mae_x = float('inf')

    # Desempate: Prophet gana si el error es similar (es más interpretable)
    if mae_x < (mae_p * 0.95): # XGB debe ser al menos 5% mejor para ganar
        return 'xgboost'
    return 'prophet'

# --- FUNCIÓN PRINCIPAL ---

def train_and_predict_competitive(df_sku: pd.DataFrame, horizon_days: int, 
                                  params: Dict) -> Dict[str, Any]:
    """Entrada principal del pipeline."""
    
    # Limpieza Timezone
    if 'ds' in df_sku.columns:
        df_sku['ds'] = pd.to_datetime(df_sku['ds']).dt.tz_localize(None)
    
    df_clean = df_sku.sort_values('ds')
    
    # Cold Start
    if len(df_clean) < 30:
        val = df_clean['y'].mean() * horizon_days
        return {'yhat': float(val), 'model': 'AVG_SIMPLE'}

    # TORNEO
    winner = _evaluate_champion(df_clean, horizon_days, params)
    
    # EJECUCIÓN GANADOR
    prediction = 0.0
    
    if winner == 'xgboost':
        try:
            df_feat = _create_xgb_features(df_clean).dropna()
            feat_cols = [c for c in df_feat.columns if c not in ['ds', 'y']]
            # Entrenamiento Final
            model = XGBRegressor(n_estimators=150, learning_rate=0.05, n_jobs=1)
            model.fit(df_feat[feat_cols], df_feat['y'])
            # Predicción Futura
            prediction = _predict_xgb_recursive(model, df_clean, horizon_days)
        except Exception as e:
            logger.error(f"XGBoost falló en predicción final: {e}. Revertiendo a Prophet.")
            winner = 'prophet' # Fallback
            
    if winner == 'prophet':
        prediction = _train_prophet(df_clean, horizon_days, params)
        
    return {
        'yhat': float(prediction),
        'yhat_lower': float(prediction * 0.8), # Intervalos simples por ahora
        'yhat_upper': float(prediction * 1.2),
        'model': winner.upper() # Retornamos quién ganó para estadísticas
    }

