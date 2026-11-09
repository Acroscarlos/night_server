#!/usr/bin/env python
# coding: utf-8

import pandas as pd
import requests
import re
import json
import urllib3
import os
from datetime import datetime, timedelta
import time
import streamlit as st
from src.logger_config import get_custom_logger
logger = get_custom_logger("cashea_precios", "cashea_precios.log")
# Suprimir las advertencias de seguridad
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from pathlib import Path
from src.utils.json_core import SafeJSONEncoder


# ==========================================
# 📂 CONFIGURACIÓN DE DIRECTORIOS
# ==========================================
# Subimos 3 niveles: inventario_Auto -> src -> AA_PRINCIPAL_V6_PROJECT
BASE_DIR = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True) # Crea la carpeta si no existe

# Redirigimos el archivo de caché a la carpeta output
CACHE_FILE = str(OUTPUT_DIR / "cashea_sku_cache.csv")





def cargar_variables_globales_desde_json(archivo='config.json'):
    """
    Lee el archivo config.json y carga todas las variables en el script,
    manteniendo sus nombres y mayúsculas originales.
    """
    logger.info(f"Cargando configuración desde '{archivo}'...")
    try:
        with open(archivo, 'r') as f:
            config = json.load(f)
        
        # Iterar sobre cada sección del JSON (woocommerce, loyverse, etc.)
        for seccion in config.values():
            # globals().update() es la función que crea las variables en el script
            globals().update(seccion)
            
        logger.info(" Todas las variables de configuración han sido cargadas.")
        return True
        
    except FileNotFoundError:
        logger.error(f" ERROR: No se encontró el archivo de configuración '{archivo}'. Asegúrate de que esté en la misma carpeta.")
        return False
    except Exception as e:
        logger.error(f" ERROR al leer el archivo de configuración: {e}")
        return False



# ==============================================================================
# 2. FUNCIONES DE OBTENCIÓN Y CACHÉ DE DATOS DE CASHEA
# ==============================================================================

def extract_sku_from_description(description):
    """Extrae un SKU alfanumérico de la descripción."""
    if not isinstance(description, str): return None
    match = re.search(r'[Ss][Kk][Uu]\s*:\s*([a-zA-Z0-9]+)', description)
    return match.group(1) if match else None

def obtener_mapeo_con_precios_cashea():
    """
    Obtiene el mapeo SKU -> ID e-kommers, precio actual y NOMBRE, usando un archivo CSV como caché.
    """
    if os.path.exists(CACHE_FILE):
        file_mod_time = datetime.fromtimestamp(os.path.getmtime(CACHE_FILE))
        if datetime.now() - file_mod_time < timedelta(days=CACHE_EXPIRATION_DAYS):
            logger.info(f" caché '{CACHE_FILE}' es reciente. Cargando desde archivo.")
            return pd.read_csv(CACHE_FILE, dtype={'SKU': str, 'ID_Cashea': str})

    logger.info(f" caché no existe o ha expirado. Generando desde la API de Cashea...")
    
    endpoint = f"/v2/products/{MERCHANT_NAME}"
    url = BASE_URL_GET + endpoint
    headers = {'Authorization': f'ApiKey {API_KEY}', 'accept': 'application/json'}
    all_products = []
    page=1

    while True:
        params = {'limit': 100, 'offset': (page-1)*100}
        try:
            response = requests.get(url, headers=headers, params=params, verify=False, timeout=8)
            if response.status_code != 200:
                logger.error(f"❌ Error al obtener productos de Cashea: {response.text}")
                return None
            data = response.json()
            page_products = data.get('elements', [])
            if not page_products: break
            all_products.extend(page_products)
            page += 1
            
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            logger.warning(f"⚠️ Timeout o Caída de Red en Cashea (Omitiendo sincronización): {e}")
            return None
            
        except Exception as e:
            logger.error(f"❌ Error de conexión al obtener productos de Cashea: {e}")
            return None
    
    df_cashea_full = pd.DataFrame(all_products)
    df_cashea_full['SKU'] = df_cashea_full['description'].apply(extract_sku_from_description)
    
    # RESTAURADO: Incluir 'name' en el dataframe de mapeo
    df_mapeo = df_cashea_full.dropna(subset=['SKU'])[['SKU', 'externalId', 'price', 'name']]
    df_mapeo.rename(columns={
        'externalId': 'ID_Cashea', 
        'price': 'Precio_Actual_Cashea',
        'name': 'Nombre_Cashea'
    }, inplace=True)
    
    df_mapeo['SKU'] = df_mapeo['SKU'].astype(str).str.strip().str.upper()
    df_mapeo['ID_Cashea'] = df_mapeo['ID_Cashea'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
    
    df_mapeo.to_csv(CACHE_FILE, index=False)
    logger.info(f"✅ Mapeo de SKUs, precios y nombres guardado en '{CACHE_FILE}'.")
    
    # 🔍 Log de integridad del catálogo
    total_cashea = len(df_cashea_full)
    con_sku = len(df_mapeo)
    logger.info(f"📊 Diagnóstico Catálogo Cashea: Se descargaron {total_cashea} items totales, de los cuales {con_sku} tienen un SKU válido en la descripción.")
    if con_sku == 0:
        logger.error("🚨 ATENCIÓN: No se detectaron SKUs en Cashea. Revisa el formato 'SKU: XXXX' en las descripciones.")
    
    return df_mapeo

# ==============================================================================
# 3. FUNCIÓN DE ENVÍO A LA API DE CASHEA
# ==============================================================================
def enviar_actualizacion_cashea_en_lotes(products_list, tipo_actualizacion="ActualizarPrecio"):
    """Envía la lista de actualizaciones a la API de Cashea en lotes seguros de 25."""
    if not products_list:
        logger.info(f"\nNo hay productos para la actualización de tipo '{tipo_actualizacion}'.")
        return

    url = f"{BASE_URL_GET}/upload-stock?merchantName={MERCHANT_NAME}"
    headers = {'Authorization': f'ApiKey {API_KEY}', 'Content-Type': 'application/json', 'accept': 'application/json'}
    
    tamaño_lote = 25 # RESTAURADO: Regresado a 25 para evitar error 413 (Payload Too Large)
    total_productos = len(products_list)
    logger.info(f"\nIniciando envío de {total_productos} actualizaciones de '{tipo_actualizacion}' a Cashea...")

    for i in range(0, total_productos, tamaño_lote):
        lote = products_list[i:i + tamaño_lote]
        payload = {"products": lote, "merchantName": MERCHANT_NAME, "merchantToUpdateStock": MERCHANT_NAME}
        payload_seguro = json.dumps(payload, cls=SafeJSONEncoder)
        logger.info(f"\n🚀 Enviando lote {i//tamaño_lote + 1} ('{tipo_actualizacion}')...")
        
        
        
        try:
            response = requests.post(url, headers=headers, data=payload_seguro, verify=False)
            if response.status_code in [200, 201]:
                # 🛡️ Diagnóstico de aplicación real
                if response.text == "[]":
                    logger.warning(f"⚠️ Instrucción RECIBIDA por Cashea pero NO APLICADA (Respuesta vacía []). Revisa si los IDs existen en Cashea.")
                else:
                    logger.info(f"✅ ¡Lote ('{tipo_actualizacion}') actualizado exitosamente (Código {response.status_code})!")
            else:
                logger.error(f"❌ ERROR en lote ('{tipo_actualizacion}'). Código: {response.status_code}\nRespuesta: {response.text}")




        except Exception as e:
            logger.error(f"❌ Ocurrió un error de conexión en el lote ('{tipo_actualizacion}'): {e}")
        time.sleep(1)

def enviar_actualizacion_individual_cashea(products_list, tipo_actualizacion="PonerPrecio99999"):
    """
    RESTAURADO: Envía actualizaciones a la API de Cashea UNO POR UNO para máxima fiabilidad en productos sin stock.
    """
    if not products_list:
        logger.info(f"\nNo hay productos para la actualización de tipo '{tipo_actualizacion}'.")
        return

    url = f"{BASE_URL_GET}/upload-stock?merchantName={MERCHANT_NAME}"
    headers = {'Authorization': f'ApiKey {API_KEY}', 'Content-Type': 'application/json', 'accept': 'application/json'}
    
    total_productos = len(products_list)
    logger.info(f"\nIniciando envío de {total_productos} actualizaciones de '{tipo_actualizacion}' (UNO A UNO)...")

    for i, product_payload in enumerate(products_list):
        payload = {
            "products": [product_payload], 
            "merchantName": MERCHANT_NAME, 
            "merchantToUpdateStock": MERCHANT_NAME
        }
        payload_seguro = json.dumps(payload, cls=SafeJSONEncoder)
        logger.info(f"🚀 Enviando item {i+1} de {total_productos} (ID: {product_payload['ID e-kommers']})...")
        try:
            response = requests.post(url, headers=headers, data=payload_seguro, verify=False)
            if response.status_code in [200, 201]:
                # 🛡️ Diagnóstico de aplicación real
                if response.text == "[]":
                    logger.warning(f"⚠️ Item {product_payload['ID e-kommers']} RECIBIDO pero NO ENCONTRADO por Cashea (Respuesta []).")
                else:
                    logger.info(f"✅ ¡ÉXITO! Item actualizado correctamente.")
            else:
                logger.error(f"❌ ERROR. Código: {response.status_code}\nRespuesta: {response.text}")
        except Exception as e:
            logger.error(f"❌ Ocurrió un error de conexión: {e}")
        
        time.sleep(1) # Pausa estricta de 1 segundo





# ==============================================================================
# 4. EJECUCIÓN MANUAL (LLAMADO DESDE DASHBOARD)
# ==============================================================================
def ejecutar_precios_cashea(config, ruta_csv):
    """Ejecuta la actualización manual de precios desde un CSV para Cashea."""

    if not os.path.exists(ruta_csv):
        st.error(f"Falta el archivo de actualización. No se encontró ningún documento en la ruta: {ruta_csv}", icon="🚨")
        st.info("💡 Consejo: Asegúrate de descargar el reporte actualizado y colocarlo en la carpeta correcta antes de sincronizar.")
        logger.error(f"Ejecución cancelada: No se encontró el archivo CSV en {ruta_csv}")
        return None  # Salida limpia, sin crash.


    logger.info(f"\n--- Iniciando Actualización Manual de Precios Cashea ---")
    
    for seccion, valores in config.items():
        if isinstance(valores, dict):
            # 🛡️ INTERCEPTOR DE RUTAS: Redirige todo 'archivos_y_cache' a la carpeta output
            if seccion == "archivos_y_cache":
                for clave, valor in valores.items():
                    if isinstance(valor, str):
                        # Usa el OUTPUT_DIR que ya definiste al inicio de tu script
                        valores[clave] = str(OUTPUT_DIR / valor)
            
            globals().update(valores)
            globals()[seccion] = valores

    try:
        # 🛡️ Ajustamos los defaults a tus nuevos nombres de columna en la matriz
        
        col_sku = globals().get('COLUMNA_SKU', 'sku')
        col_precio = globals().get('COLUMNA_PRECIO', 'Precio_PVP')
        # Buscamos la columna de título, si no existe usamos un default para evitar errores
        col_nombre = globals().get('COLUMNA_NOMBRE', 'nombre') 

        logger.info(f"Cargando datos desde '{ruta_csv}'...")
        # Leemos el CSV asegurando que el SKU sea texto
        df_precios_nuevos = pd.read_csv(ruta_csv, dtype={col_sku: str})
        
        # 🛡️ ESCUDO ANTI-KEYERROR: Validar que las columnas realmente existan en el CSV
        if col_sku not in df_precios_nuevos.columns or col_precio not in df_precios_nuevos.columns:
            st.error(f"❌ El CSV no tiene la estructura correcta. Faltan las columnas `{col_sku}` o `{col_precio}`.")
            st.warning(f"Columnas detectadas en tu archivo: {', '.join(df_precios_nuevos.columns)}")
            logger.error(f"Faltan columnas. Esperadas: {col_sku}, {col_precio}. Encontradas: {list(df_precios_nuevos.columns)}")
            return None # Salida limpia, aborta la actualización
            
        # Preparamos las columnas asegurando que existan
        columnas_renombrar = {col_sku: 'SKU', col_precio: 'Precio_Nuevo'}
        if col_nombre in df_precios_nuevos.columns:




            columnas_renombrar[col_nombre] = 'Nombre_CSV'
        else:
            df_precios_nuevos['Nombre_CSV'] = 'N/A' # Fallback si el CSV no trae título
            
        df_precios_nuevos.rename(columns=columnas_renombrar, inplace=True)
        


























        # 🛡️ LIMPIEZA EXTREMA DEL CSV ANTES DEL CRUCE (VERSIÓN 2 DECIMALES CASHEA)
        df_precios_nuevos['SKU'] = df_precios_nuevos['SKU'].astype(str).str.strip().str.upper()
        
        # 1. Conversión segura: Solo cambiamos la coma por punto (por si se guardó en Excel ES)
        # y NO borramos el punto original para proteger los decimales.
        df_precios_nuevos['Precio_Nuevo'] = pd.to_numeric(
            df_precios_nuevos['Precio_Nuevo'].astype(str).str.replace(',', '.', regex=False),
            errors='coerce'
        )
        
        # 2. Forzamos matemáticamente el redondeo a 2 decimales (.00)
        df_precios_nuevos['Precio_Nuevo'] = df_precios_nuevos['Precio_Nuevo'].round(2)
        
        df_precios_nuevos.dropna(subset=['SKU', 'Precio_Nuevo'], inplace=True)

        df_mapeo_cashea = obtener_mapeo_con_precios_cashea()
        
        if df_mapeo_cashea is not None:
            # RESTAURADO: El Cruce Outer (Identifica los que faltan)
            df_comparacion = pd.merge(
                df_precios_nuevos, 
                df_mapeo_cashea, 
                on='SKU', 
                how='outer', 
                indicator=True
            )

            # RESTAURADO: Generación de Reporte de Auditoría
            logger.info("Generando reporte de comparación...")
            df_reporte = df_comparacion.rename(columns={
                'Nombre_CSV': 'Nombre Articulo (CSV)',
                'Precio_Nuevo': 'Precio (CSV)',
                'Nombre_Cashea': 'Nombre Articulo (Cashea)',
                'Precio_Actual_Cashea': 'Precio Actual (Cashea)'
            })
            columnas_reporte = ['Nombre Articulo (CSV)', 'SKU', 'Precio (CSV)', 
                                'Nombre Articulo (Cashea)', 'Precio Actual (Cashea)', 'ID_Cashea']
            
            # Filtramos solo las columnas que existan para evitar KeyError
            cols_finales = [col for col in columnas_reporte if col in df_reporte.columns]
            
            # Construimos la ruta segura hacia la carpeta output
            ruta_reporte = OUTPUT_DIR / "Comparacion_Precios_Cashea.csv"
            
            df_reporte[cols_finales].to_csv(ruta_reporte, index=False, encoding='utf-8-sig')
            logger.info(f"✅ Reporte de comparación guardado en '{ruta_reporte}'.")

            # RESTAURADO: Regla A - Grupo A: Poner a 99999 (Existen en Cashea pero NO en el CSV)
            df_poner_99999 = df_comparacion[df_comparacion['_merge'] == 'right_only']
            lista_payload_99999 = [{
                "ID e-kommers": str(row['ID_Cashea']).strip(), 
                "Precio": 99999.00
            } for _, row in df_poner_99999.iterrows()]
            logger.info(f"Se marcaron {len(lista_payload_99999)} productos para poner precio 99999 (no están en el CSV).")

            
            
            
            # RESTAURADO: Regla B - Grupo B: Actualizar Precio Normal
            df_actualizar = df_comparacion[df_comparacion['_merge'] == 'both'].copy()
            df_actualizar['Precio_Nuevo'] = df_actualizar['Precio_Nuevo'].round(2)
            df_actualizar['Precio_Actual_Cashea'] = pd.to_numeric(df_actualizar['Precio_Actual_Cashea'], errors='coerce').round(2)
            
            # 🛡️ FILTRO ESTRICTO: Previene el Error 400. Cashea rechaza lotes enteros si un solo precio es 0.
            df_actualizar_filtrado = df_actualizar[
                (df_actualizar['Precio_Nuevo'] != df_actualizar['Precio_Actual_Cashea']) & 
                (df_actualizar['Precio_Nuevo'] > 0)
            ]
            
            lista_payload_update = [{
                "ID e-kommers": str(row['ID_Cashea']).strip(), 
                "Precio": float(row['Precio_Nuevo']) # DEBE MANTENERSE COMO FLOAT
            } for _, row in df_actualizar_filtrado.iterrows()]











            logger.info(f"Se marcaron {len(lista_payload_update)} productos para actualizar precio (diferente al CSV).")

            # --- ENVÍOS ---
            # 📊 Resumen Ejecutivo de Sincronización
            logger.info(f"📈 RESUMEN DE CAMBIOS: [{len(lista_payload_update)}] productos subirán/bajarán de precio. [{len(lista_payload_99999)}] productos se pondrán en 99999 (No están en matriz).")

            # 1. Enviar los que se quedan sin stock (UNO A UNO)
            if lista_payload_99999:
                enviar_actualizacion_individual_cashea(lista_payload_99999, tipo_actualizacion="PonerPrecio99999")

            # 2. Enviar los precios actualizados (EN LOTES)
            if lista_payload_update:
                enviar_actualizacion_cashea_en_lotes(lista_payload_update, tipo_actualizacion="ActualizarPrecio")
            elif not lista_payload_99999:
                logger.info("ℹ️ No se detectaron diferencias de precio. No es necesario enviar actualizaciones.")
            
    except FileNotFoundError:
        logger.error(f"❌ Error: No se encontró el archivo: '{ruta_csv}'")
    except Exception as e:
        logger.error(f"❌ Ocurrió un error inesperado en el orquestador: {e}")

    logger.info("\nProceso de actualización de precios para Cashea finalizado.")