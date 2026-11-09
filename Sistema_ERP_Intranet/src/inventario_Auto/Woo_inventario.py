#!/usr/bin/env python
# coding: utf-8

import pandas as pd
from woocommerce import API
import requests
import time
import json
import datetime
from requests.exceptions import RequestException
from src.logger_config import get_custom_logger
from src.utils.json_core import SafeJSONEncoder
logger = get_custom_logger("woo_inv", "woo_inventario.log")



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

def cargar_variables_globales_desde_json(archivo='config.json.txt'):
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
# 3. FUNCIONES DE WOOCOMMERCE
# ==============================================================================

def obtener_mapeo_con_stock_actual():
    """
    Descarga todos los productos y variaciones para crear un mapa:
    SKU -> {parent_id, variation_id, stock_actual}
    """
    sku_map = {}
    page = 1
    



    while True:
        try:
            # Quitamos el timeout=8 porque ya está definido en la configuración principal (timeout=30)
            resp_prod = wcapi.get("products", params={"per_page": 100, "page": page})
            products = resp_prod.json()
            if not products: break

            for product in products:
                product_id = product['id']
                if product['type'] == 'simple' and product.get('sku'):
                    sku_map[str(product['sku'])] = {'parent_id': product_id, 'variation_id': 0, 'stock_actual': product.get('stock_quantity'), 'stock_status': product.get('stock_status')}
                
                elif product['type'] == 'variable':
                    # Aquí también lo quitamos
                    resp_var = wcapi.get(f"products/{product_id}/variations", params={"per_page": 100})




                    variations = resp_var.json()
                    for variation in variations:
                        if variation.get('sku'):
                            sku_map[str(variation['sku'])] = {'parent_id': product_id, 'variation_id': variation['id'], 'stock_actual': variation.get('stock_quantity'), 'stock_status': variation.get('stock_status')}
            page += 1
            
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            logger.warning(f"⚠️ Timeout o Caída de Red en WooCommerce (Se omitirá para no congelar ERP): {e}")
            return None
            
        except Exception as e:
            logger.error(f" Error al obtener productos/variaciones: {e}")
            return None
    
    logger.info(f"\n Mapeo completo con {len(sku_map)} SKUs.")
    return sku_map


def procesar_inventario_por_marcas(df_inv_actual, marcas_permitidas):
    """
    Filtra el inventario de Loyverse.
    - Mantiene el stock para las marcas en la lista 'marcas_permitidas'.
    - Pone en 0 el stock para todas las demás marcas.
    """
    
    df_procesado = df_inv_actual.copy()
    
    # 1. Extraer la marca del nombre del artículo (asumiendo que es la primera palabra)
    #    Se convierte a mayúsculas para una comparación sin distinción de mayús./minús.
    df_procesado['Marca'] = df_procesado['nombre_del_articulo'].str.split().str[0].str.upper()
    
    # Convertir la lista de marcas permitidas a mayúsculas también
    marcas_permitidas_upper = [marca.upper() for marca in marcas_permitidas]
    
    # 2. Identificar las filas cuyas marcas NO están en la lista permitida
    filtro_marcas_a_excluir = ~df_procesado['Marca'].isin(marcas_permitidas_upper)
    
    # 3. Poner en 0 el 'stock_total' para esas filas
    df_procesado.loc[filtro_marcas_a_excluir, 'stock_total'] = 0
    
    # Se elimina la columna temporal 'Marca' antes de devolver el dataframe
    return df_procesado.drop(columns=['Marca'])

def sincronizar_inventario_diferencial(df_loyverse, sku_map):
    """
    Compara stocks, prepara y envía actualizaciones solo para los productos desactualizados,
    en lotes de 50.
    """
    updates_simples = []
    updates_variables = {}


    # --- (Esta parte de preparación no cambia) ---
    for _, row in df_loyverse.iterrows():
        sku = str(row['SKU']).strip()
        stock_objetivo_wc = max(0, row['stock_total'] - RESERVA_STOCK_WOO)
        
        if sku in sku_map:
            map_info = sku_map[sku]
            stock_actual_wc = map_info['stock_actual']
            stock_actual_wc_int = int(stock_actual_wc) if stock_actual_wc is not None else -999
            
            # NUEVO: Extraer estado actual y calcular el estado objetivo
            estado_actual_wc = map_info.get('stock_status', '')
            estado_objetivo_wc = "instock" if stock_objetivo_wc > 0 else "outofstock"

            # Validar SI el número es distinto O SI el estado de texto es incorrecto
            if stock_objetivo_wc != stock_actual_wc_int or estado_objetivo_wc != estado_actual_wc:
                logger.info(f"  - Desactualizado: SKU {sku} (Objetivo: {stock_objetivo_wc} '{estado_objetivo_wc}', Actual en WC: {stock_actual_wc} '{estado_actual_wc}'). Se actualizará.")
                parent_id = map_info['parent_id']
                variation_id = map_info['variation_id']
                item_id = variation_id if variation_id != 0 else parent_id
                
                # Inyectar el estado forzado en el payload
                update_payload = {
                    "id": item_id, 
                    "manage_stock": True, 
                    "stock_quantity": stock_objetivo_wc, 
                    "stock_status": estado_objetivo_wc
                }
                
                if variation_id == 0:
                    updates_simples.append(update_payload)
                else:
                    if parent_id not in updates_variables: updates_variables[parent_id] = []
                    updates_variables[parent_id].append(update_payload)
            
    # --- ENVIAR ACTUALIZACIONES EN LOTES ---
    
    # --- MODIFICACIÓN 1: Definir tamaño de lote ---
    tamaño_lote = 50 

    if updates_simples:
        total_simples = len(updates_simples)
        logger.info(f"\nIniciando envío de {total_simples} productos simples en lotes de {tamaño_lote}.")
        
        # --- MODIFICACIÓN 2: Bucle para enviar lotes de simples ---
        for i in range(0, total_simples, tamaño_lote):
            lote_simples = updates_simples[i:i + tamaño_lote]
            num_lote_actual = (i // tamaño_lote) + 1
            num_lotes_total = (total_simples - 1) // tamaño_lote + 1
            logger.info(f"\n  Enviando lote {num_lote_actual} de {num_lotes_total} (Simples)...")
            try:
                # 🛡️ LAVADO DE DATOS: Pasa por el codificador para purgar tipos Numpy o NaNs
                payload_bruto = {"update": lote_simples}
                payload_limpio = json.loads(json.dumps(payload_bruto, cls=SafeJSONEncoder))
                
                # El endpoint es el de productos, en lote
                wcapi.post("products/batch", payload_limpio).json()
                logger.info(f"    Lote {num_lote_actual} de simples actualizado.")

            except Exception as e:
                logger.error(f"    Error al actualizar lote {num_lote_actual} de simples: {e}")
            time.sleep(1) # Pausa entre lotes

    if updates_variables:
        logger.info(f"\nIniciando envío de actualizaciones para variaciones de {len(updates_variables)} productos variables...")
        
        for parent_id, variations_list in updates_variables.items():
            total_variaciones = len(variations_list)
            logger.info(f"\n  - Actualizando {total_variaciones} variaciones para producto ID {parent_id} en lotes de {tamaño_lote}...")
            
            # --- MODIFICACIÓN 3: Bucle para enviar lotes de variaciones ---
            for i in range(0, total_variaciones, tamaño_lote):
                lote_variaciones = variations_list[i:i + tamaño_lote]
                num_lote_actual = (i // tamaño_lote) + 1
                num_lotes_total = (total_variaciones - 1) // tamaño_lote + 1
                logger.info(f"    Enviando lote {num_lote_actual} de {num_lotes_total} (Variaciones Prod. {parent_id})...")
                try:
                    # 🛡️ LAVADO DE DATOS: Pasa por el codificador para purgar tipos Numpy o NaNs
                    payload_bruto = {"update": lote_variaciones}
                    payload_limpio = json.loads(json.dumps(payload_bruto, cls=SafeJSONEncoder))

                    # El endpoint es el de variaciones, en lote, específico para el producto padre
                    wcapi.post(f"products/{parent_id}/variations/batch", payload_limpio).json()
                    logger.info(f"      Lote {num_lote_actual} de variaciones para producto {parent_id} actualizado.")
                except Exception as e:
                    logger.error(f"      Error al actualizar lote {num_lote_actual} de variaciones para producto {parent_id}: {e}")
                time.sleep(1) # Pausa entre lotes de variaciones

    if not updates_simples and not updates_variables:
        logger.info("¡Inventario completamente sincronizado! No se requieren actualizaciones.")


# ==============================================================================
# 4. EJECUCIÓN DEL SCRIPT (LLAMADO DESDE EL ORQUESTADOR V8)
# ==============================================================================
def ejecutar_sincronizacion_woo(config, df_inventario_vivo):
    """Función principal para ser llamada por el orquestador cada 5 minutos."""
    
    logger.info("--- Iniciando Sincronización Automática WooCommerce (Inyección de Datos) ---")

    from pathlib import Path
    BASE_DIR_PROYECTO = Path(__file__).resolve().parent.parent.parent
    CARPETA_OUTPUT = BASE_DIR_PROYECTO / "output"
    CARPETA_OUTPUT.mkdir(parents=True, exist_ok=True)

    try:
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
                
        # Asegurarnos de que las variables de Loyverse estén disponibles globalmente
        global BASE_URL, ACCESS_TOKEN
        ACCESS_TOKEN = config['api'].get('loyverse_token', globals().get('ACCESS_TOKEN'))
        BASE_URL = config['api'].get('loyverse_base_url', globals().get('BASE_URL', "https://api.loyverse.com/v1.0"))

        logger.info(f"Conectando a WooCommerce en: {globals().get('WEBSITE_URL')}")

        # 2. Inicializar wcapi como variable GLOBAL para que el resto del script la use
        global wcapi
        wcapi = API(
            url=globals().get('WEBSITE_URL'),
            consumer_key=globals().get('CONSUMER_KEY'),
            consumer_secret=globals().get('CONSUMER_SECRET'),
            version="wc/v3",
            timeout=30
        )
        logger.info("Cliente de la API de WooCommerce inicializado correctamente.")

        # --- 🚨 ADAPTACIÓN ESTRICTA DEL DATA CONTRACT (COMPATIBILIDAD) 🚨 ---
        logger.info("Recibiendo inventario en vivo desde el Orquestador maestro...")
        df = df_inventario_vivo.copy()
        
        # Renombramos para que tu lógica antigua procesar_inventario_por_marcas no colapse
        df.rename(columns={
            'sku': 'SKU',
            'nombre': 'nombre_del_articulo'
        }, inplace=True)
        
        # Seguro anti-caídas: Si hay datos, procesamos
        if not df.empty:
            # 🛡️ CORRECCIÓN BUG TRANSITO: WooCommerce solo debe recibir Stock Físico (sin tránsito)
            df['transito'] = pd.to_numeric(df.get('cantidad_en_transito', 0), errors='coerce').fillna(0)
            df['stock_fisico_real'] = (df['stock_total'] - df['transito']).clip(lower=0)
            
            # Reemplazamos la columna 'stock_total' engañosa por el stock puro
            # para que el resto del script (sincronizar_inventario_diferencial) no tenga que cambiar
            df['stock_total'] = df['stock_fisico_real']
            
            # Traemos la lista de marcas de las variables globales inyectadas
            marcas = globals().get('marcas_a_sincronizar', [])
            inv_actual = procesar_inventario_por_marcas(df, marcas)
            
            mapa_completo = obtener_mapeo_con_stock_actual()
            
            if mapa_completo:
                sincronizar_inventario_diferencial(inv_actual, mapa_completo)
                
    except Exception as e:
        logger.error(f"Ocurrió un error en el flujo principal de WooCommerce: {e}", exc_info=True)

    logger.info("Proceso de sincronización con WooCommerce finalizado.")