#!/usr/bin/env python
# coding: utf-8


import pandas as pd
from woocommerce import API
import requests
import time
import json
import os
import streamlit as st
from src.logger_config import get_custom_logger
from src.utils.json_core import SafeJSONEncoder
logger = get_custom_logger("woo_precios", "woo_precios.log")


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
# 2. FUNCIONES DE WOOCOMMERCE
# ==============================================================================

def obtener_mapeo_sku_a_id():
    """
    Descarga todos los productos/variaciones de WooCommerce y devuelve un
    mapa simple de SKU -> ID para la actualización.
    """
    logger.info("Obteniendo mapa SKU -> ID desde WooCommerce...")
    sku_map = {}
    page = 1


    while True:
        try:
            # Quitamos el timeout=8 porque ya está definido en la configuración principal (timeout=30)
            resp_prod = wcapi.get("products", params={"per_page": 100, "page": page})
            products = resp_prod.json()
            if not products: break
            
            for product in products:
                if product.get('sku'): sku_map[str(product['sku'])] = product['id']
                if product['type'] == 'variable':
                    # Aquí también lo quitamos
                    resp_var = wcapi.get(f"products/{product['id']}/variations", params={"per_page": 100})
                    variations = resp_var.json()
                    for variation in variations:
                        if variation.get('sku'): sku_map[str(variation['sku'])] = variation['id']
            logger.info(f"Procesados {len(products)} productos principales. Total SKUs mapeados: {len(sku_map)}")
            page += 1
            
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            logger.warning(f"⚠️ Timeout o Caída de Red en WooCommerce (Omitido): {e}")
            return None
            
        except Exception as e:
            logger.error(f"❌ Error al obtener productos/variaciones: {e}")
            return None
        



    logger.info("✅ Mapeo de SKUs completado.")
    return sku_map

def actualizar_precios_por_lotes(df_precios, sku_id_map, batch_size=100):
    """
    Envía actualizaciones masivas a WooCommerce utilizando el endpoint /products/batch.
    """
    import math
    logger.info(f"\nIniciando actualización BATCH para {len(df_precios)} productos...")

    col_sku = globals().get('COLUMNA_SKU', 'sku')
    col_precio = globals().get('COLUMNA_PRECIO', 'Precio_PVP')
    umbral = globals().get('UMBRAL_COTIZACION', 999999) # 🛡️ Rescatamos tu regla de negocio

    actualizaciones = []

    for index, row in df_precios.iterrows():
        sku = str(row[col_sku])
        
        if sku in sku_id_map:
            producto_id = sku_id_map[sku]
            precio_flotante = float(row[col_precio])
            
            # 🎯 TU LÓGICA INTACTA: Umbral de Cotización
            if precio_flotante > umbral:
                precio_limpio = ""
                logger.info(f"  - SKU {sku} > ${umbral}. Se enviará en blanco (Cotización).")
            else:
                precio_limpio = str(row[col_precio])

            # WooCommerce requiere obligatoriamente el "id" dentro del objeto
            actualizaciones.append({
                "id": producto_id,
                "regular_price": precio_limpio
            })
        else:
            logger.info(f"  - ⏭️ SKU '{sku}' omitido: No encontrado en WooCommerce.")

    if not actualizaciones:
        logger.warning("No hay productos válidos para actualizar.")
        return

    # Motor de Empaquetado (Chunking) en lotes de 100
    total_lotes = math.ceil(len(actualizaciones) / batch_size)

    for i in range(total_lotes):
        inicio_idx = i * batch_size
        fin_idx = inicio_idx + batch_size
        lote_actual = actualizaciones[inicio_idx:fin_idx]

        # 🛡️ LA ESTRUCTURA EXACTA REQUERIDA POR EL ENDPOINT BATCH
        payload = {
            "update": lote_actual
        }

        try:
            logger.info(f"Enviando lote {i+1} de {total_lotes} ({len(lote_actual)} productos)...")
            
            # Limpiamos con tu SafeJSONEncoder para evitar errores de serialización
            payload_limpio = json.loads(json.dumps(payload, cls=SafeJSONEncoder))
            
            # Ejecutamos el POST masivo
            respuesta = wcapi.post("products/batch", payload_limpio)

            if respuesta.status_code in [200, 201]:
                logger.info(f"✅ Lote {i+1} actualizado exitosamente en WooCommerce.")
            else:
                logger.error(f"❌ Error en lote {i+1}. Código: {respuesta.status_code}. Detalle: {respuesta.text}")

        except Exception as e:
            logger.error(f"❌ Excepción crítica en la red durante el lote {i+1}: {str(e)}")

        # Micro-pausa de 1.5s entre lotes de 100 para no tumbar la base de datos de WordPress
        time.sleep(1.5)

    logger.info("✅ Sincronización BATCH completada.")

# ==============================================================================
# 3. EJECUCIÓN MANUAL (LLAMADO DESDE DASHBOARD)
# ==============================================================================
def ejecutar_precios_woo(config, ruta_csv):
    """Ejecuta la actualización manual de precios desde un CSV."""
    
    if not os.path.exists(ruta_csv):
        st.error(f"Falta el archivo de actualización. No se encontró ningún documento en la ruta: {ruta_csv}", icon="🚨")
        st.info("💡 Consejo: Asegúrate de descargar el reporte actualizado y colocarlo en la carpeta correcta antes de sincronizar.")
        logger.error(f"Ejecución cancelada: No se encontró el archivo CSV en {ruta_csv}")
        return None  # Salida limpia, sin crash.
    
    
    logger.info(f"\n--- Iniciando Actualización Manual de Precios Woo ---")
    
    from pathlib import Path
    BASE_DIR_PROYECTO = Path(__file__).resolve().parent.parent.parent
    CARPETA_OUTPUT = BASE_DIR_PROYECTO / "output"
    CARPETA_OUTPUT.mkdir(parents=True, exist_ok=True)
    
    # 1. Inyectar variables e interceptar rutas del config
    for seccion, valores in config.items():
        if isinstance(valores, dict):
            # 🛡️ INTERCEPTOR DE RUTAS: Redirige todo 'archivos_y_cache' a la carpeta output
            if seccion == "archivos_y_cache":
                for clave, valor in valores.items():
                    if isinstance(valor, str):
                        valores[clave] = str(CARPETA_OUTPUT / valor)
            
            globals().update(valores)
            globals()[seccion] = valores
    
    # 2. Declarar wcapi como GLOBAL para que obtener_mapeo_sku_a_id() pueda usarla
    global wcapi
    try:
        wcapi = API(
            url=config['woocommerce']['WEBSITE_URL'],
            consumer_key=config['woocommerce']['CONSUMER_KEY'],
            consumer_secret=config['woocommerce']['CONSUMER_SECRET'],
            version="wc/v3",
            timeout=30
        )
    except Exception as e:
        logger.error(f"❌ Error al inicializar la API de WooCommerce: {e}")
        return

    try:
        # 🛡️ Ajustamos los defaults a tus nuevos nombres de columna en la matriz
        col_sku = globals().get('COLUMNA_SKU', 'sku')
        col_precio = globals().get('COLUMNA_PRECIO', 'Precio_PVP')

        # --- PASO 1: Leer y limpiar el CSV ---
        logger.info(f"Cargando datos desde '{ruta_csv}'...")
        df_precios_nuevos = pd.read_csv(ruta_csv, dtype={col_sku: str})
        
        # 🛡️ ESCUDO ANTI-KEYERROR: Validar que las columnas realmente existan en el CSV
        if col_sku not in df_precios_nuevos.columns or col_precio not in df_precios_nuevos.columns:
            st.error(f"❌ El CSV no tiene la estructura correcta. Faltan las columnas `{col_sku}` o `{col_precio}`.")
            st.warning(f"Columnas detectadas en tu archivo: {', '.join(df_precios_nuevos.columns)}")
            logger.error(f"Faltan columnas. Esperadas: {col_sku}, {col_precio}. Encontradas: {list(df_precios_nuevos.columns)}")
            return None # Salida limpia, aborta la actualización

        df_precios_nuevos.dropna(subset=[col_sku, col_precio], inplace=True)
        












        # 1. Forzamos a Float nativo (coercionando errores a NaN)
        df_precios_nuevos[col_precio] = pd.to_numeric(df_precios_nuevos[col_precio], errors='coerce')
        
        # 🛡️ Mantenemos tu dropna original en lugar del fillna del cuaderno para evitar enviar $0.0 por error
        df_precios_nuevos.dropna(subset=[col_precio], inplace=True)

        # 2. Woo exige los precios como string sin separador de miles. 
        # Redondeamos a 2 decimales para evitar derivas de coma flotante en la API:
        df_precios_nuevos[col_precio] = df_precios_nuevos[col_precio].round(2).astype(str)

        # --- PASO 2: Obtener el mapa de SKUs que realmente existen en WooCommerce ---
        mapa_sku_id = obtener_mapeo_sku_a_id()
        
        if mapa_sku_id:
            # --- PASO 3: Filtrar el CSV para actualizar solo los SKUs existentes ---
            df_para_actualizar = df_precios_nuevos[df_precios_nuevos[col_sku].isin(mapa_sku_id.keys())]
            
            # --- PASO 4: Enviar las actualizaciones por Lotes (Batch) ---
            actualizar_precios_por_lotes(df_para_actualizar, mapa_sku_id)
            
    except FileNotFoundError:
        logger.error(f"❌ Error: No se encontró el archivo en la ruta especificada: '{ruta_csv}'")
    except Exception as e:
        logger.error(f"❌ Ocurrió un error inesperado: {e}")

    logger.info("\nProceso de actualización WooCommerce finalizado.")