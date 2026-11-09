#!/usr/bin/env python
# coding: utf-8

import os
import time
import logging
import json
import requests
import pandas as pd
from typing import List, Dict, Optional
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import uuid
from src.database.db_core import SessionLocal
from src.database.models import Product, InventoryLocation, InventoryMovement, AuditLog
from src.services.loyverse_api import LoyverseAPI

seguridad_logger = logging.getLogger("erp_security_auditor")


# Configuración de Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("data_ingestion")

# Constantes (Idealmente deberían venir de variables de entorno)
BASE_URL = "https://api.loyverse.com/v1.0"

ACCESS_TOKEN = os.getenv("LOYVERSE_TOKEN") 

if not ACCESS_TOKEN:
    # Esto es una medida de seguridad: Si alguien intenta correr este módulo 
    # sin pasar por main.py (que carga el config), lanzará error.
    logger.warning("⚠️ LOYVERSE_TOKEN no detectado en variables de entorno. Las llamadas a la API fallarán.")

def _get_session() -> requests.Session:
    """
    Configura una sesión HTTP con estrategia de reintentos automática.
    Función auxiliar para mantener fetch_api_data limpia.
    """
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.headers.update({'Authorization': f'Bearer {ACCESS_TOKEN}'})
    return session

def fetch_api_data(endpoint: str, params: Optional[Dict] = None) -> List[Dict]:
    """
    Obtiene datos de la API de Loyverse manejando paginación y reintentos.
    
    Args:
        endpoint (str): Endpoint de la API (ej: '/items').
        params (dict): Parámetros opcionales para la query.

    Returns:
        List[Dict]: Lista completa de objetos recuperados.
    """
    url = f"{BASE_URL}{endpoint}"
    all_data: List[Dict] = []
    cursor: Optional[str] = None
    session = _get_session()
    current_params = params.copy() if params else {}
    limit = 250  # Límite máximo permitido por Loyverse

    logger.info(f"Iniciando descarga desde: {endpoint}")

    try:
        while True:
            current_params['limit'] = limit
            if cursor:
                current_params['cursor'] = cursor

            response = session.get(url, params=current_params, timeout=(5, 45))
            response.raise_for_status()
            data = response.json()

            # Lógica para encontrar la clave que contiene la lista de datos
            # Excluye la clave 'cursor' para encontrar la data real (ej: 'items', 'receipts')
            keys = [k for k in data.keys() if k != 'cursor']
            if not keys:
                break # Respuesta vacía o formato inesperado
            
            data_key = keys[0]
            page_items = data.get(data_key, [])
            
            if not page_items:
                break

            all_data.extend(page_items)
            cursor = data.get('cursor')
            
            logger.debug(f"Página procesada. Total acumulado: {len(all_data)}")

            if not cursor:
                break
                
            time.sleep(0.2) # Respetar rate limits

    except requests.exceptions.RequestException as e:
        logger.error(f"Error crítico de red en {endpoint}: {str(e)}")
        # Dependiendo de la regla de negocio, podríamos retornar lo parcial o fallar.
        # Aquí elegimos fallar para garantizar integridad.
        raise RuntimeError(f"Fallo en la ingesta de API: {e}")

    logger.info(f"Descarga completada de {endpoint}: {len(all_data)} registros.")
    return all_data

def load_sales_history(filepath: str) -> pd.DataFrame:
    """
    Carga el histórico de ventas y asegura tipos de datos correctos.

    Args:
        filepath (str): Ruta al archivo CSV.

    Returns:
        pd.DataFrame: DataFrame con fechas en UTC y columnas tipadas.
    """
    if not os.path.exists(filepath):
        logger.warning(f"Archivo histórico no encontrado: {filepath}. Se retorna DF vacío.")
        return pd.DataFrame()

    try:
        # Forzamos tipos para evitar errores de memoria o interpretación
        df = pd.read_csv(filepath, dtype={'id': str, 'sku': str, 'variant_id': str})
        
        if 'created_at' in df.columns:
            # Estandarización estricta a UTC
            df['created_at'] = pd.to_datetime(df['created_at'], utc=True)
        else:
            logger.error("El CSV histórico no tiene columna 'created_at'.")
            return pd.DataFrame()

        logger.info(f"Histórico cargado: {len(df)} registros desde {filepath}")
        return df

    except (pd.errors.EmptyDataError, pd.errors.ParserError) as e:
        logger.error(f"Error al leer CSV histórico {filepath}: {e}")
        return pd.DataFrame()
    except Exception as e:
        logger.critical(f"Error inesperado cargando histórico: {e}")
        raise

def load_purchase_orders(directory: str) -> pd.DataFrame:
    """
    Consolida todos los CSVs de órdenes de compra en un directorio.

    Args:
        directory (str): Ruta a la carpeta de órdenes.

    Returns:
        pd.DataFrame: DataFrame consolidado.
    """
    if not os.path.exists(directory):
        logger.error(f"Directorio de órdenes no existe: {directory}")
        return pd.DataFrame()

    all_dfs: List[pd.DataFrame] = []
    
    try:
        files = [f for f in os.listdir(directory) if f.lower().endswith('.csv')]
        
        if not files:
            logger.info("No hay órdenes de compra pendientes.")
            return pd.DataFrame()

        for filename in files:
            path = os.path.join(directory, filename)
            try:
                # Se asume estructura estándar, pero se fuerza SKU/REF a string
                df_temp = pd.read_csv(path, dtype=str)
                # Convertir cantidad a numérico, coercing errors a NaN (luego 0)
                if 'Cantidad' in df_temp.columns:
                     df_temp['Cantidad'] = pd.to_numeric(df_temp['Cantidad'], errors='coerce').fillna(0)
                all_dfs.append(df_temp)
            except Exception as e:
                logger.warning(f"Omitiendo archivo corrupto {filename}: {e}")

        if not all_dfs:
            return pd.DataFrame()

        consolidated = pd.concat(all_dfs, ignore_index=True)
        logger.info(f"Órdenes consolidadas: {len(consolidated)} líneas de {len(files)} archivos.")
        return consolidated

    except Exception as e:
        logger.error(f"Error procesando directorio de órdenes: {e}")
        return pd.DataFrame()

def sync_loyverse_sales_to_local():
    """Sincroniza las ventas del POS de Loyverse como salidas de inventario locales."""
    loyverse_api = LoyverseAPI()
    receipts = loyverse_api.get_recent_receipts(limit=50)

    if not receipts:
        return

    db = SessionLocal()
    try:
        ubicaciones_loyverse = db.query(InventoryLocation).filter(InventoryLocation.loyverse_store_id != None).all()
        map_store_loc = {loc.loyverse_store_id: loc.id for loc in ubicaciones_loyverse}

        for receipt in receipts:
            receipt_number = str(receipt.get("receipt_number"))
            store_id = receipt.get("store_id")
            line_items = receipt.get("line_items", [])

            # 1. Evitar el Doble Gasto (Comprobar si ya se sincronizó)
            ya_procesado = db.query(AuditLog).filter(
                AuditLog.accion == "SYNC_VENTA_POS",
                AuditLog.registro_id == receipt_number
            ).first()

            if ya_procesado:
                continue

            # 2. Validar que la tienda exista en nuestro ERP
            source_loc_id = map_store_loc.get(store_id)
            if not source_loc_id:
                continue

            try:
                # 3. Procesar líneas del recibo
                for item in line_items:
                    sku = item.get("sku")
                    item_qty = float(item.get("quantity", 0.0))

                    if not sku or item_qty <= 0:
                        continue

                    producto = db.query(Product).filter(Product.sku == sku).first()
                    if not producto:
                        continue

                    # Insertar SIN forzar el ID (la BD lo autogenera)
                    mov_venta = InventoryMovement(
                        product_id=producto.id,
                        source_location_id=source_loc_id,
                        dest_location_id=None,
                        cantidad=item_qty,
                        tipo="Venta_POS",
                        status="Completado",
                        created_by="loyverse_sync_job"
                    )
                    db.add(mov_venta)

                # 4. Dejar marca de auditoría inmutable SIN forzar el ID
                audit_log = AuditLog(
                    tabla_afectada="inventory_movements",
                    registro_id=receipt_number,
                    accion="SYNC_VENTA_POS",
                    usuario="loyverse_sync_job",
                    detalles_json={"store_id": store_id, "items_count": len(line_items)}
                )
                db.add(audit_log)
                
                db.commit() # Commit por recibo
                
            except Exception as inner_e:
                db.rollback()
                seguridad_logger.error(f"Fallo sincronizando recibo {receipt_number}: {str(inner_e)}")

    except Exception as e:
        seguridad_logger.error(f"Error crítico en sync_loyverse_sales_to_local: {str(e)}", exc_info=True)
    finally:
        db.close()


