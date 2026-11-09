#!/usr/bin/env python
# coding: utf-8

# In[1]:


import os
import json
import logging
import pandas as pd
from datetime import datetime
from woocommerce import API
import urllib3

# Desactivar advertencias SSL si no hay certificados configurados localmente
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# CONFIGURACIÓN DE RUTAS Y LOGS
# ==========================================
BASE_DIR = "/home/night/Sistema_ERP_Intranet"
RUTA_CONFIG = os.path.join(BASE_DIR, "config", "config.json")
RUTA_CSV_PROMO = os.path.join(BASE_DIR, "output", "df_inventario_promo.csv")
RUTA_LOGS = os.path.join(BASE_DIR, "logs", "sincronizador_promos.log")

# Asegurar directorio de logs
os.makedirs(os.path.dirname(RUTA_LOGS), exist_ok=True)

logging.basicConfig(
    filename=RUTA_LOGS,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def parse_date(date_val):
    if pd.isna(date_val) or not str(date_val).strip():
        return None
    return datetime.strptime(str(date_val), "%Y-%m-%d %H:%M:%S")

def ejecutar_sincronizacion_diaria():
    logger.info("=== INICIANDO SINCRONIZADOR DIARIO DE PROMOCIONES WOOCOMMERCE ===")
    
    # 1. Cargar Configuración y Conectar a WooCommerce
    try:
        with open(RUTA_CONFIG, 'r') as f:
            woo_creds = json.load(f).get("woocommerce", {})
            
        wcapi = API(
            url=woo_creds.get('WEBSITE_URL'),
            consumer_key=woo_creds.get('CONSUMER_KEY'),
            consumer_secret=woo_creds.get('CONSUMER_SECRET'),
            version="wc/v3",
            timeout=30
        )
    except Exception as e:
        logger.error(f"Fallo al inicializar API Woo o leer JSON: {e}")
        return

    # 2. Leer Matriz de Promociones (Generada por WOO_planificador_promos.py)
    if not os.path.exists(RUTA_CSV_PROMO):
        logger.warning("No se encontró df_inventario_promo.csv. Se procederá solo a limpiar huérfanos.")
        df_ganadores = pd.DataFrame()
    else:
        try:
            df_promos = pd.read_csv(RUTA_CSV_PROMO)
            df_ganadores = df_promos[df_promos['seleccionado_promo'] == 'x'].copy()
            logger.info(f"Matriz leída. Ítems programados encontrados: {len(df_ganadores)}")
        except Exception as e:
            logger.error(f"Error al leer la matriz CSV: {e}")
            df_ganadores = pd.DataFrame()

    # 3. Evaluar Reloj y Construir Payload Batch para Ganadores Legítimos
    ahora = datetime.now()
    paquete_actualizaciones = []
    ids_legitimos_hoy = [] # Lista para guardar los que SÍ deben estar activos hoy
    
    for _, row in df_ganadores.iterrows():
        sku = str(row['sku']).strip()
        
        # Buscar ID interno
        try:
            resp = wcapi.get("products", params={"sku": sku}).json()
            if not resp:
                logger.warning(f"Ignorado: SKU {sku} no encontrado en WooCommerce.")
                continue
            producto_id = resp[0]['id']
        except Exception as e:
            logger.error(f"Error de conexión buscando SKU {sku}: {e}")
            continue

        # Extraer fechas y precios
        inicio = parse_date(row.get('fecha_inicio'))
        fin = parse_date(row.get('fecha_fin'))
        inicio_flash = parse_date(row.get('fecha_inicio_flash'))
        fin_flash = parse_date(row.get('fecha_fin_flash'))
        inicio_cont = parse_date(row.get('fecha_inicio_continuacion'))
        fin_cont = parse_date(row.get('fecha_fin_continuacion'))

        precio_estandar = row.get('precios_promo_woo')
        precio_flash = row.get('precio_flash')

        # Reloj Evaluador
        precio_a_enviar = "" 
        
        if inicio_flash and fin_flash and (inicio_flash <= ahora <= fin_flash) and not pd.isna(precio_flash):
            precio_a_enviar = str(precio_flash)
        elif inicio and fin and (inicio <= ahora <= fin) and not pd.isna(precio_estandar):
            precio_a_enviar = str(precio_estandar)
        elif inicio_cont and fin_cont and (inicio_cont <= ahora <= fin_cont) and not pd.isna(precio_estandar):
            precio_a_enviar = str(precio_estandar)

        paquete_actualizaciones.append({
            "id": producto_id,
            "sale_price": precio_a_enviar
        })
        
        # Si la decisión de hoy es tener una promoción activa, lo anotamos en la lista de legítimos
        if precio_a_enviar != "":
            ids_legitimos_hoy.append(producto_id)
            
        estado = "FLASH" if precio_a_enviar == str(precio_flash) else ("NORMAL" if precio_a_enviar else "APAGADO")
        logger.info(f"SKU: {sku} | Estado: {estado} | Precio Inyectado: '{precio_a_enviar}'")

    # =========================================================
    # 4. NUEVO MÓDULO: AUDITOR DE HUÉRFANOS (Barrer y Limpiar)
    # =========================================================
    logger.info("Buscando promociones huérfanas activas en WooCommerce...")
    try:
        # Traemos SOLO los productos que WooCommerce tiene en oferta actualmente
        # per_page=100 asegura que escanee un buen volumen de productos de un solo golpe
        productos_en_oferta = wcapi.get("products", params={"on_sale": True, "per_page": 100}).json()
        
        huerfanos_encontrados = 0
        for prod in productos_en_oferta:
            prod_id = prod['id']
            # Si el producto web está en oferta, pero NO es parte de nuestros legítimos de hoy
            if prod_id not in ids_legitimos_hoy:
                # Verificamos si no lo hemos metido ya en el paquete para apagarlo
                if not any(item['id'] == prod_id for item in paquete_actualizaciones):
                    paquete_actualizaciones.append({
                        "id": prod_id,
                        "sale_price": ""  # Instrucción para matar la oferta
                    })
                    huerfanos_encontrados += 1
                    logger.info(f"🧹 Huérfano detectado y apagado: ID {prod_id} (SKU: {prod.get('sku', 'N/A')})")
                    
        logger.info(f"Total de huérfanos detectados e inyectados al Batch: {huerfanos_encontrados}")
    except Exception as e:
        logger.error(f"Error crítico al ejecutar el Auditor de Huérfanos: {e}")

    # 5. Inyección Masiva a WooCommerce
    if paquete_actualizaciones:
        logger.info(f"🚀 Disparando Batch con {len(paquete_actualizaciones)} actualizaciones totales (Legítimos + Huérfanos)...")
        try:
            respuesta_batch = wcapi.post("products/batch", {"update": paquete_actualizaciones}).json()
            actualizados = respuesta_batch.get('update', [])
            errores = respuesta_batch.get('errors', [])
            
            logger.info(f"¡ÉXITO! Ítems actualizados en WooCommerce: {len(actualizados)}")
            if errores:
                logger.warning(f"Ítems con error devuelto por Woo: {len(errores)}")
        except Exception as e:
            logger.error(f"Error crítico al enviar el paquete Batch: {e}")
    else:
        logger.info("No se armó ningún paquete. Fin de la ejecución.")

if __name__ == "__main__":
    ejecutar_sincronizacion_diaria()


# In[ ]:




