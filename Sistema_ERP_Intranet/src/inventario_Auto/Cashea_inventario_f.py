#!/usr/bin/env python
# coding: utf-8


import json
import pandas as pd
import requests
import re
import time
import urllib3
from datetime import datetime, timedelta
import os
import datetime

from src.logger_config import get_custom_logger
from src.utils.json_core import SafeJSONEncoder
logger = get_custom_logger("cashea_inv", "cashea_inv.log")

# Suprimir las advertencias de seguridad
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
# Data_global['RESERVA_STOCK_CASHEA']
# Data_global['RESERVA_STOCK_WOO']


TIENDA_MILLENNIUM_ID = "48c795d9-0e59-4dce-9411-17a339c44ec8"
TIENDA_GALERIAS_ID = "8058e6a0-60aa-4305-8629-55e281c6ef16" 

def obtener_inventario_detallado_por_tienda():
    """
    Combina los datos de '/inventory' y '/items' para crear un reporte
    de stock detallado por producto y por tienda.
    """
    raw_inventory_levels = fetch_all_from_api('/inventory', 'inventory_levels')
    if raw_inventory_levels is None:
        return None

    inventario_por_variante = {}
    for level in raw_inventory_levels:
        variant_id = level.get('variant_id')
        store_id = level.get('store_id')
        in_stock = level.get('in_stock', 0)
        
        if variant_id not in inventario_por_variante:
            inventario_por_variante[variant_id] = {}
        inventario_por_variante[variant_id][store_id] = in_stock

    items_list = fetch_all_from_api('/items', 'items')
    if items_list is None:
        return None

    # Unir toda la información
    stock_base = []
    for item in items_list:
        item_name = item.get('item_name')
        for variant in item.get('variants', []):
            variant_id = variant.get('variant_id')
            sku = variant.get('sku')
            
            # Buscar el stock de esta variante en nuestro diccionario procesado
            stock_info = inventario_por_variante.get(variant_id, {})
            
            stock_galerias = stock_info.get(TIENDA_GALERIAS_ID, 0)
            stock_millennium = stock_info.get(TIENDA_MILLENNIUM_ID, 0)
            
            stock_base.append({
                'nombre_del_articulo': item_name,
                'variant_id': variant_id,
                'SKU': sku,
                'stock_gale': stock_galerias,
                'stock_mile': stock_millennium,
                'stock_total' : stock_millennium + stock_galerias
            })

    df_final = pd.DataFrame(stock_base)
    return df_final

def fetch_all_from_api(endpoint, data_key, max_retries=5):
    """
    Función genérica y RESILIENTE para obtener datos con paginación por cursor.
    Implementa Exponential Backoff para tolerar caídas de red y Rate Limits (HTTP 429).
    """
    headers = {'Authorization': f'Bearer {ACCESS_TOKEN}'}
    all_data = []
    cursor = None

    while True:
        url = f"{BASE_URL}{endpoint}"
        params = {'limit': 250}
        if cursor:
            params['cursor'] = cursor

        attempt = 0
        success = False

        # Bucle interno: Retries con Exponential Backoff para UNA SOLA página
        while attempt < max_retries and not success:
            try:
                # 🛡️ TIMEOUT de 15s para evitar que el hilo se quede colgado infinitamente
                response = requests.get(url, headers=headers, params=params, timeout=15)

                # 🛡️ MANEJO DE RATE LIMIT (Demasiadas peticiones)
                if response.status_code == 429:
                    # Lee cuánto tiempo pide la API que esperemos, o usa el backoff
                    retry_after = int(response.headers.get("Retry-After", 2 ** attempt))
                    logger.warning(f"⚠️ Rate Limit (429) en {endpoint}. Pausando {retry_after}s...")
                    time.sleep(retry_after)
                    attempt += 1
                    continue

                # Lanza excepción si el código es 4xx o 5xx (excepto 429 que ya manejamos)
                response.raise_for_status()

                data = response.json()
                page_data = data.get(data_key, [])
                
                all_data.extend(page_data)
                cursor = data.get('cursor')
                success = True  # ¡Página descargada con éxito! Rompe el bucle de reintentos.

            except RequestException as e:
                # 🛡️ EXPONENTIAL BACKOFF: Espera 1s, luego 2s, luego 4s, 8s...
                wait_time = (2 ** attempt)
                logger.error(f"❌ Fallo de red en {endpoint} (cursor: {cursor}): {e}. Reintento {attempt+1}/{max_retries} en {wait_time}s...")
                time.sleep(wait_time)
                attempt += 1

        if not success:
            # 🛡️ PREVENCIÓN DE CORRUPCIÓN: Si falló 5 veces, abortamos y devolvemos None.
            # No devolvemos datos parciales para evitar que el ERP ponga stock 0 a lo que faltó descargar.
            logger.critical(f"🚨 Aborto total. Imposible descargar {endpoint} tras {max_retries} intentos. Se cancela el ciclo para proteger el inventario.")
            return None

        # Si no hay más páginas (cursor vacío o no trajo datos), terminamos la descarga completa
        if not cursor or not page_data:
            break

    return all_data


# ==============================================================================
# BLOQUE 1: CONFIGURACIÓN Y FUNCIONES AUXILIARES
# ==============================================================================

def cargar_configuracion(archivo='config.json.txt'):
    """Carga la configuración desde un archivo JSON."""
    print(f"Cargando configuración desde '{archivo}'...")
    logger.info(f"Cargando configuración desde '{archivo}'...")
    try:
        with open(archivo, 'r') as f:
            config = json.load(f)
        for key, value in config.items():
            globals()[key] = value
        logger.info("Todas las variables de configuración han sido cargadas.")
        print(" Todas las variables de configuración han sido cargadas.")
        return True
    except Exception as e:
        logger.error(f"ERROR al leer el archivo de configuración: {e}", exc_info=True)
        print(f" ERROR al leer el archivo de configuración: {e}")
        return False

def procesar_inventario_por_marcas(df_inv_actual):
    """Pone en 0 el stock de las marcas que no están en la lista 'marcas_a_sincronizar'."""
    df_procesado = df_inv_actual.copy()
    marcas_permitidas = reglas_negocio.get('marcas_a_sincronizar', [])
    
    if not marcas_permitidas:
        logger.warning("La lista 'marcas_a_sincronizar' está vacía. Se establecerá todo el inventario a 0.")
        print(" La lista 'marcas_a_sincronizar' está vacía. Se establecerá todo el inventario a 0.")
        df_procesado['stock_total'] = 0
        return df_procesado

    df_procesado['Marca'] = df_procesado['nombre_del_articulo'].str.split().str[0].str.upper()
    marcas_permitidas_upper = [marca.upper() for marca in marcas_permitidas]
    filtro_marcas_a_excluir = ~df_procesado['Marca'].isin(marcas_permitidas_upper)
    
    df_procesado.loc[filtro_marcas_a_excluir, 'stock_total'] = 0
    logger.info("Inventario de Loyverse procesado.")
    print(" Inventario de Loyverse procesado.")
    return df_procesado.drop(columns=['Marca'])

def obtener_mapeo_cashea():
    """Obtiene el mapa SKU -> ID_Cashea desde la API de Cashea."""
    logger.info("Obteniendo mapeo de productos desde Cashea...")
    print("Obteniendo mapeo de productos desde Cashea...")
    endpoint = f"/v2/products/{cashea['MERCHANT_NAME']}"
    url = cashea['BASE_URL_GET'] + endpoint
    headers = {'Authorization': f"ApiKey {cashea['API_KEY']}", 'accept': 'application/json'}
    all_products = []
    page = 1



    while True:
        params = {'limit': 100, 'offset': (page-1)*100}
        try:
            # 🛡️ INYECCIÓN DE TIMEOUT (8 Segundos)
            response = requests.get(url, headers=headers, params=params, verify=False, timeout=8)
            if response.status_code != 200:
                logger.error(f"Error al obtener productos de Cashea: {response.text}")
                print(f" Error al obtener productos de Cashea: {response.text}")
                return None
            data = response.json()
            page_products = data.get('elements', [])
            if not page_products: break
            all_products.extend(page_products)
            page += 1
            
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            # 🛡️ CAPTURA AISLADA DE CAÍDA DE RED (Abandona sin colapsar el hilo principal)
            logger.warning(f"⚠️ Timeout/Error de red en Cashea (Omitiendo sincronización): {e}")
            print(f" ⚠️ Timeout/Error de red en Cashea (Omitiendo sincronización): {e}")
            return None
            
        except Exception as e:
            logger.error(f"Error de conexión al obtener productos de Cashea: {e}", exc_info=True)
            print(f" Error de conexión al obtener productos de Cashea: {e}")
            return None
            


    df_cashea_full = pd.DataFrame(all_products)
    df_cashea_full['SKU'] = df_cashea_full['description'].apply(lambda d: re.search(r'[Ss][Kk][Uu]\s*:\s*([a-zA-Z0-9]+)', str(d)).group(1) if re.search(r'[Ss][Kk][Uu]\s*:\s*([a-zA-Z0-9]+)', str(d)) else None)
    df_mapeo = df_cashea_full.dropna(subset=['SKU'])[['SKU', 'externalId']]
    df_mapeo.rename(columns={'externalId': 'ID_Cashea'}, inplace=True)
    df_mapeo['SKU'] = df_mapeo['SKU'].astype(str)
    logger.info(f"Mapeo de Cashea obtenido para {len(df_mapeo)} productos con SKU.")
    print(f" Mapeo de Cashea obtenido para {len(df_mapeo)} productos con SKU.")
    return df_mapeo

# ==============================================================================
# BLOQUE 2: LÓGICA DE ACTUALIZACIÓN (SEPARADA)
# ==============================================================================

def actualizar_stock_con_valor(products_list):
    """Función para enviar actualizaciones de stock con valores específicos."""
    if not products_list:
        logger.info("No hay productos con stock > 0 para actualizar.")
        print("No hay productos con stock > 0 para actualizar.")
        return

    url = f"{cashea['BASE_URL_GET']}/upload-stock?merchantName={cashea['MERCHANT_NAME']}"
    headers = {'Authorization': f"ApiKey {cashea['API_KEY']}", 'Content-Type': 'application/json'}
    tamaño_lote = 100
    total_productos = len(products_list)
    logger.info(f"Iniciando envío de {total_productos} actualizaciones de stock...")
    print(f"Iniciando envío de {total_productos} actualizaciones de stock...")

    for i in range(0, total_productos, tamaño_lote):
        lote = products_list[i:i + tamaño_lote]
        payload = {"products": lote, "merchantName": cashea['MERCHANT_NAME'], "merchantToUpdateStock": cashea['MERCHANT_NAME']}
        payload_seguro = json.dumps(payload, cls=SafeJSONEncoder)
        logger.info(f"Enviando lote de stock con valor {i//tamaño_lote + 1}...")
        print(f"Enviando lote de stock con valor {i//tamaño_lote + 1}...")
        try:
            response = requests.post(url, headers=headers, data=payload_seguro, verify=False)
            if response.status_code in [200, 201]:
                logger.info("¡ÉXITO! Lote actualizado correctamente.")
                print(f" ¡ÉXITO! Lote actualizado correctamente.")
            else:
                logger.error(f"ERROR en lote. Código: {response.status_code} Respuesta: {response.text}")
                print(f" ERROR en lote. Código: {response.status_code}\nRespuesta: {response.text}")
        except Exception as e:
            logger.error(f"Ocurrió un error de conexión en el lote: {e}", exc_info=True)
            print(f" Ocurrió un error de conexión en el lote: {e}")
        time.sleep(1)

def actualizar_stock_a_cero_en_lotes(id_list):
    """
    Toma una lista de IDs y envía actualizaciones en lotes para poner su stock a 0.
    (Esta es la función que confirmaste que funciona).
    """
    if not id_list:
        logger.error(f"No hay productos para poner en stock 0.")
        logger.info("No hay productos para poner en stock 0.")
        return

    products_to_update = [{"ID e-kommers": product_id, "Inventario real": 0} for product_id in id_list]
    url = f"{cashea['BASE_URL_GET']}/upload-stock?merchantName={cashea['MERCHANT_NAME']}"
    headers = {'Authorization': f"ApiKey {cashea['API_KEY']}", 'Content-Type': 'application/json'}
    tamaño_lote = 25
    total_productos = len(products_to_update)
    logger.info(f"Iniciando envío de actualizaciones para poner {total_productos} productos a stock 0...")

    for i in range(0, total_productos, tamaño_lote):
        lote = products_to_update[i:i + tamaño_lote]
        payload = {"products": lote, "merchantToUpdateStock": cashea['MERCHANT_NAME'], "merchantName": cashea['MERCHANT_NAME']}
        payload_seguro = json.dumps(payload, cls=SafeJSONEncoder)
        logger.info(f"Enviando lote para poner en cero {i//tamaño_lote + 1}...")
        try:
            response = requests.post(url, headers=headers, data=payload_seguro, verify=False)
            if response.status_code in [200, 201]:
                logger.info(f" ¡ÉXITO! Lote 'cero' actualizado correctamente.")
            else:
                logger.error(f" ERROR en lote 'cero'. Código: {response.status_code}\nRespuesta: {response.text}")
        except requests.exceptions.RequestException as e:
            logger.error(f" Ocurrió un error de conexión en el lote: {e}")
        time.sleep(1)



# ==============================================================================
# 4. EJECUCIÓN DEL SCRIPT (LLAMADO DESDE EL ORQUESTADOR V8)
# ==============================================================================


def ejecutar_sincronizacion_cashea(config, df_inventario_vivo):
    """Función principal para ser llamada por el orquestador cada 5 minutos."""
    
    logger.info("--- Iniciando Sincronización Automática Cashea (Inyección de Datos) ---")

    from pathlib import Path
    BASE_DIR_PROYECTO = Path(__file__).resolve().parent.parent.parent
    CARPETA_OUTPUT = BASE_DIR_PROYECTO / "output"
    CARPETA_OUTPUT.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Inyectar variables del config.json y aplicar Interceptor de Rutas
        for seccion, valores in config.items():
            if isinstance(valores, dict):
                # 🛡️ INTERCEPTOR DE RUTAS
                if seccion == "archivos_y_cache":
                    for clave, valor in valores.items():
                        if isinstance(valor, str):
                            valores[clave] = str(CARPETA_OUTPUT / valor)
                
                # Inyectamos tanto el diccionario completo (ej. cashea['API_KEY']) 
                # como las variables sueltas por retrocompatibilidad
                globals()[seccion] = valores
                globals().update(valores)
            
        # 2. Asignar credenciales exactas como las tenías
        global BASE_URL, ACCESS_TOKEN



        BASE_URL = loyverse['BASE_URL']
        ACCESS_TOKEN = loyverse['ACCESS_TOKEN']

        # --- 🚨 ADAPTACIÓN ESTRICTA DEL DATA CONTRACT (COMPATIBILIDAD) 🚨 ---
        logger.info("Recibiendo inventario en vivo desde el Orquestador maestro...")
        inv_actual_raw = df_inventario_vivo.copy()
        
        # Renombramos para que tu lógica antigua no se rompa (Evita KeyError)
        inv_actual_raw.rename(columns={
            'sku': 'SKU',
            'nombre': 'nombre_del_articulo'
        }, inplace=True)

        # Seguro anti-caídas: Procesamos solo si el df no está vacío
        if not inv_actual_raw.empty:
            # 1. Aplicar lógica de marcas al inventario
            inv_actual_procesado = procesar_inventario_por_marcas(inv_actual_raw)
            
            # 2. Obtener el mapeo de SKUs de Cashea
            df_mapeo_cashea = obtener_mapeo_cashea()
            
            if df_mapeo_cashea is not None:
                # 3. Unir Loyverse con Cashea para saber qué IDs corresponden a qué SKUs
                df_final = pd.merge(inv_actual_procesado, df_mapeo_cashea, on='SKU', how='inner')

                # 4. Dividir en dos grupos: los que tienen stock y los que van a cero
                df_con_stock = df_final[df_final['stock_total'] > 0].copy()
                df_sin_stock = df_final[df_final['stock_total'] == 0].copy()

                # 5. Preparar los payloads para cada grupo
                # Grupo CON STOCK: se aplica la regla de reserva
                payload_con_stock = []
                if not df_con_stock.empty:
                    df_con_stock['stock_objetivo'] = (df_con_stock['stock_total'] - Data_global['RESERVA_STOCK_CASHEA']).clip(lower=0)
                    # NOTA DE SEGURIDAD: Se aplica int() al inventario real para evitar que Cashea rechace floats (ej. 2.0)
                    payload_con_stock = [{"ID e-kommers": row['ID_Cashea'], "Inventario real": int(row['stock_objetivo'])} for _, row in df_con_stock.iterrows()]
                
                # Grupo SIN STOCK: se crea la lista de IDs
                lista_ids = []
                if not df_sin_stock.empty:
                    lista_ids = df_sin_stock['ID_Cashea'].tolist()
                
                # Logs unificados y corregidos
                logger.info(f"Resumen Cashea: Se actualizará el stock de {len(payload_con_stock)} productos. Se forzará a 0 el stock de {len(lista_ids)} productos.")

                # 6. Ejecutar las dos actualizaciones de forma separada
                actualizar_stock_con_valor(payload_con_stock)
                actualizar_stock_a_cero_en_lotes(lista_ids)

    except Exception as e:
        logger.error(f"Ocurrió un error en el flujo principal de Cashea: {e}", exc_info=True)
        
    logger.info("Proceso de Sincronización con Cashea Finalizado.")