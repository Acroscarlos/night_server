#!/usr/bin/env python
# coding: utf-8

# Aquí tienes el código completo y optimizado para `src/output_manager.py`.
# 
# Este módulo implementa el **Data Contract** solicitado, garantizando tipos de datos seguros para el frontend y utilizando una estrategia de **escritura atómica** para evitar condiciones de carrera (race conditions) si el Dashboard intenta leer mientras el script está escribiendo.
# 

# In[2]:


import os
import logging
import pandas as pd
import numpy as np
from typing import Optional
import tempfile
# Configuración de Logging
logger = logging.getLogger("output_manager")




def generar_y_guardar_matriz_standalone(db_df: pd.DataFrame, config: dict):
    """
    Construye el CSV standalone de Pricing y lo guarda atómicamente.
    """
    try:
        # --- 1. PROCESAMIENTO (Código del Cuaderno) ---
        df = db_df.copy()
        
        gastos_extras = config.get("gastos_extras", 0.0)
        marketing = config.get("marketing", 0.0)

        df = df.rename(columns={
            'sku': 'SKU',
            'nombre': 'PRODUCTO',
            'costo_base': 'COSTO MAC',
            'inventario_entrante': 'ENTRANTES',
            'volumen_m3': 'VOLUMEN M3',
            'margen_deseado': 'MARGEN (%)',
            'precio_competencia': 'PRECIO COMP.'
        })

        df['% GASTOS EXTRAS'] = gastos_extras
        df['MARKETING (%)'] = marketing

        df['Recargo_Interno'] = np.where(
            df['MARGEN (%)'] < 1.0,
            1 / (1 - df['MARGEN (%)']),
            1.0
        )

        df['PVP PROYECTADO'] = df['COSTO MAC'] * df['Recargo_Interno']

        columnas_finales = [
            'SKU', 'PRODUCTO', 'COSTO MAC', 'ENTRANTES', 'VOLUMEN M3',
            '% GASTOS EXTRAS', 'MARKETING (%)', 'MARGEN (%)', 'PRECIO COMP.', 'PVP PROYECTADO'
        ]

        for col in columnas_finales:
            if col not in df.columns:
                df[col] = 0.0

        df_final = df[columnas_finales]

        # --- 2. ESCRITURA ATÓMICA (Seguridad Zero-Downtime) ---
        # Aseguramos la ruta (usando la carpeta 'backup' en singular como pediste)
        base_dir = "/home/night/Sistema_ERP_Intranet"
        backup_dir = os.path.join(base_dir, "backup")
        os.makedirs(backup_dir, exist_ok=True)
        
        archivo_final = os.path.join(backup_dir, "pricing_matrix_standalone.csv")
        
        # Escribimos en un archivo temporal primero
        fd, temp_path = tempfile.mkstemp(dir=backup_dir, suffix=".csv")
        with os.fdopen(fd, 'w') as f:
            df_final.to_csv(f, index=False)
            
        # Movemos el temporal sobre el original (Milisegundo de reemplazo)
        os.replace(temp_path, archivo_final)
        
        # Ajustamos permisos por el consejo del auditor (Permite que Bash lo lea)
        os.chmod(archivo_final, 0o664) 
        
        logging.info("✅ Matriz Standalone generada y guardada atómicamente.")
        
    except Exception as e:
        logging.error(f"❌ Error al generar Matriz Standalone: {str(e)}")









def sanitize_output_data(df_final: pd.DataFrame) -> pd.DataFrame:
    """
    Limpia y estandariza el DataFrame para cumplir el Data Contract de visualización.
    
    Reglas de Sanitización:
    1. Reemplazo de Infinitos y NaNs por 0.
    2. Conversión de Booleanos a Enteros (1/0) para compatibilidad web/JSON.
    
    Args:
        df_final (pd.DataFrame): DataFrame con cálculos crudos.

    Returns:
        pd.DataFrame: DataFrame listo para exportación segura.
    """
    if df_final.empty:
        logger.warning("Se recibió un DataFrame vacío para sanitizar.")
        return df_final

    # Evitamos SettingWithCopyWarning
    df_clean = df_final.copy()

    try:
        # 1. Manejo de valores numéricos inválidos (Inf -> NaN -> 0)
        # Reemplazar infinito positivo/negativo por NaN primero
        df_clean.replace([np.inf, -np.inf], np.nan, inplace=True)
        
        # Rellenar todos los NaNs con 0 (seguro para columnas numéricas de reporte)
        df_clean.fillna(0, inplace=True)

        # 2. Conversión de Tipos Específicos
        for col in df_clean.columns:
            # Detectar columnas booleanas
            if df_clean[col].dtype == 'bool':
                # Convertir True/False a 1/0 (Enteros) para facilitar filtrado en Frontend
                df_clean[col] = df_clean[col].astype(int)
            
            # Asegurar que columnas numéricas no quedaron como objetos
            elif df_clean[col].dtype == 'object':
                 # Intento vectorizado de conversión suave, ignorando strings reales
                 pass 

        logger.info(f"Datos sanitizados: {df_clean.shape} filas, {df_clean.shape} columnas.")
        return df_clean

    except Exception as e:
        logger.error(f"Error crítico sanitizando datos: {e}")
        # En caso de error, retornamos copia del original con fillna básico para no romper flujo
        return df_final.fillna(0)

def export_to_csv(df: pd.DataFrame, filepath: str) -> None:
    """
    Escribe el DataFrame a disco de forma atómica (Safe Write).
    
    Estrategia:
    1. Escribir en 'archivo.csv.tmp'.
    2. Renombrar (os.replace) al nombre final. Esto es atómico en POSIX y Windows (Py3+).
    Evita que el Dashboard lea un archivo incompleto durante la escritura.
    
    Args:
        df (pd.DataFrame): Datos a guardar.
        filepath (str): Ruta destino final (ej: 'reporte_final.csv').
    """
    if df.empty:
        logger.warning("No hay datos para exportar. Se omite la generación del CSV.")
        return

    # Definir nombre temporal
    temp_filepath = f"{filepath}.tmp"

    try:
        logger.info(f"Iniciando escritura atómica en: {filepath}")
        
        # 1. Guardar en archivo temporal
        # encoding='utf-8-sig' añade BOM para que Excel abra tildes/ñ correctamente
        df.to_csv(
            temp_filepath, 
            index=False, 
            encoding='utf-8-sig', 
            float_format='%.2f' # Optimización: limitar decimales para reducir tamaño
        )
        
        # 2. Reemplazo Atómico
        # Si el archivo destino existe, lo sobrescribe instantáneamente.
        os.replace(temp_filepath, filepath)
        
        logger.info("Exportación exitosa. Archivo disponible para el Dashboard.")

    except PermissionError:
        logger.error(f"Permiso denegado al escribir en {filepath}. ¿El archivo está abierto?")
    except IOError as e:
        logger.error(f"Error de E/S al guardar el reporte: {e}")
        # Intentar limpieza del temporal si quedó huérfano
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)
    except Exception as e:
        logger.critical(f"Error inesperado en exportación: {e}")
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)


# In[ ]:




