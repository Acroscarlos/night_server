#!/usr/bin/env python
# coding: utf-8

import os
import sys
import logging
import json
import pandas as pd
import numpy as np
import argparse
from pathlib import Path
from datetime import datetime, timezone
from joblib import Parallel, delayed
from sqlalchemy import func

# --- CONFIGURACIÓN GLOBAL ---
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config" / "config.json"
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
OUTPUT_DIR = BASE_DIR / "output"


if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

# Asegurar directorios
for d in [DATA_DIR, LOG_DIR, OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Archivos
HISTORY_FILE = DATA_DIR / "historico_ventas.csv"
PO_DIR = DATA_DIR / "ordenes_de_compra"
PARAMS_FILE = DATA_DIR / "best_params.json"
REPORT_FILE = OUTPUT_DIR / "reporte_final.csv"
BASE_REPORT_FILE = DATA_DIR / "reporte_base_ml.csv"

from src.logger_config import get_custom_logger
from src.database.db_core import SessionLocal
from src.database.models import PurchaseOrder, PurchaseOrderItem
from src.output_manager import generar_y_guardar_matriz_standalone
# --- FUNCIONES AUXILIARES DE TRANSFORMACIÓN (INTACTAS) ---

def _build_current_catalog(items_data: list) -> dict:
    catalog = {}
    for item in items_data:
        item_name = item.get('item_name', 'Unknown')
        for variant in item.get('variants', []):
            sku = variant.get('sku')
            if sku:
                catalog[str(sku)] = item_name
    return catalog

def _parse_inventory_levels(inventory_data: list, items_data: list, store_config: dict) -> pd.DataFrame:
    variant_to_sku = {}
    for item in items_data:
        for variant in item.get('variants', []):
            variant_to_sku[variant.get('variant_id')] = str(variant.get('sku'))

    records = []
    for level in inventory_data:
        vid = level.get('variant_id')
        sku = variant_to_sku.get(vid)
        if not sku: continue
        records.append({'sku': sku, 'store_id': level.get('store_id'), 'in_stock': level.get('in_stock', 0)})
    
    df = pd.DataFrame(records)
    if df.empty: return pd.DataFrame(columns=['sku', 'disponibilidad_galerias', 'disponibilidad_millennium'])

    pivot = df.pivot_table(index='sku', columns='store_id', values='in_stock', fill_value=0).reset_index()
    galerias_id = store_config.get('galerias_id')
    millennium_id = store_config.get('millennium_id')

    if galerias_id and galerias_id in pivot.columns:
        pivot.rename(columns={galerias_id: 'disponibilidad_galerias'}, inplace=True)
    if millennium_id and millennium_id in pivot.columns:
        pivot.rename(columns={millennium_id: 'disponibilidad_millennium'}, inplace=True)
    
    for col in ['disponibilidad_galerias', 'disponibilidad_millennium']:
        if col not in pivot.columns: pivot[col] = 0
            
    return pivot


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        logger.critical(f"[ERROR] Config no encontrada en: {CONFIG_FILE}")
        sys.exit(1)
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


# ==============================================================================
# JOB 1: PIPELINE NOCTURNO (Machine Learning Pesado - 1 vez al día)
# Conserva tu lógica V7 Intacta.
# ==============================================================================
def run_daily_ml_pipeline():

    logger = get_custom_logger("motor_lento", "orquestador_nocturno.log")
    logger.info("🌙 Iniciando Pipeline Nocturno (Machine Learning)...")

    config = load_config()
    token = config['api'].get('loyverse_token')
    if not token:
        logger.critical("[ERROR] Token no definido en config.json")
        sys.exit(1)
    os.environ['LOYVERSE_TOKEN'] = token

    try:
        from src.data_ingestion import fetch_api_data, load_sales_history, load_purchase_orders
        from src.data_processing import clean_sku_history, impute_missing_dates
        from src.forecasting_engine import get_optimal_params, train_and_predict_competitive
        from src.inventory_logic import (calculate_sales_velocity, calculate_consolidated_stock, calculate_reorder_point, determine_inventory_status)
        from src.output_manager import sanitize_output_data, export_to_csv
    except ImportError as e:
        logger.critical(f"[ERROR] Importando modulos: {e}")
        sys.exit(1)

    horizon_days = config['prediccion']['dias_horizonte']
    window_velocity = config['prediccion']['ventana_velocidad_promedio']
    lead_time = config['negocio']['dias_tiempo_entrega_proveedor']
    security_factor = config['negocio'].get('factor_seguridad', 1.0)
    store_ids_config = config['tiendas']
    
    logger.info(f">>> INICIANDO PIPELINE NOCTURNO ML (Horizonte: {horizon_days} dias) <<<")

    # 1. INGESTA
    history_path = DATA_DIR / "historico_ventas.csv"
    df_history = load_sales_history(str(history_path))
    last_date_str = df_history['created_at'].max().strftime('%Y-%m-%dT%H:%M:%S.000Z') if not df_history.empty and 'created_at' in df_history.columns else None

    logger.info(f"Última venta registrada localmente: {last_date_str}")

    params_sales = {'created_at_min': last_date_str} if last_date_str else {}
    raw_sales = fetch_api_data('/receipts', params=params_sales)
    
    new_sales_rows = []
    for receipt in raw_sales:
        receipt_date = receipt.get('created_at')
        for item in receipt.get('line_items', []):
            new_sales_rows.append({
                'id': str(item.get('id')), 
                'sku': str(item.get('sku')), 
                'item_name': item.get('item_name'),
                'quantity': item.get('quantity', 0), 
                'created_at': receipt_date
            })
    
    if new_sales_rows:
        df_new = pd.DataFrame(new_sales_rows)
        df_new['created_at'] = pd.to_datetime(df_new['created_at'], utc=True)
        
        # Filtro estricto: Solo inyectar lo que NO exista ya en el histórico
        if not df_history.empty and 'id' in df_history.columns:
            existentes = set(df_history['id'].dropna().astype(str))
            df_new = df_new[~df_new['id'].astype(str).isin(existentes)]
            
        if not df_new.empty:
            logger.info(f"Añadiendo {len(df_new)} nuevas líneas de venta al histórico.")
            df_combined = pd.concat([df_history, df_new], ignore_index=True)
            df_combined.to_csv(history_path, index=False)
            df_history = df_combined
        else:
            logger.info("Los recibos descargados ya estaban en el histórico (0 líneas nuevas).")

    raw_items = fetch_api_data('/items')
    raw_inventory = fetch_api_data('/inventory')
    catalog_map = _build_current_catalog(raw_items)
    
    df_transit = load_purchase_orders(str(PO_DIR))
    if not df_transit.empty:
        df_transit.rename(columns={'REF': 'sku', 'Cantidad': 'cantidad_en_transito'}, inplace=True)
        df_transit = df_transit.groupby('sku', as_index=False)['cantidad_en_transito'].sum()

    # 2. PROCESAMIENTO
    df_clean = clean_sku_history(df_history, catalog_map)
    df_imputed = impute_missing_dates(df_clean)

    # 3. FORECASTING
    unique_skus = list(catalog_map.keys())
    def procesar_un_sku(sku):
        df_sku = df_imputed[df_imputed['sku'] == sku].copy()
        if df_sku.empty: return None
        df_prophet = df_sku.rename(columns={'created_at': 'ds', 'quantity': 'y'})
        params = get_optimal_params(sku, str(PARAMS_FILE))
        pred = train_and_predict_competitive(df_prophet, horizon_days, params)
        return {'sku': sku, 'nombre': catalog_map.get(sku, 'Unknown'), 'prediccion_prophet': pred.get('yhat', 0.0), 'model_used': pred.get('model', 'UNKNOWN')}

    resultados = Parallel(n_jobs=-1, verbose=5)(delayed(procesar_un_sku)(sku) for sku in unique_skus)
    df_forecast = pd.DataFrame([r for r in resultados if r is not None])

    # 4. LÓGICA INVENTARIO
    sr_velocity = calculate_sales_velocity(df_clean, window_days=window_velocity)
    df_velocity = sr_velocity.reset_index(name='velocidad')
    df_stock_stores = _parse_inventory_levels(raw_inventory, raw_items, store_ids_config)
    
    df_master = df_forecast.merge(df_velocity, on='sku', how='left').fillna(0).infer_objects(copy=False)
    df_master = df_master.merge(df_stock_stores, on='sku', how='left').fillna(0).infer_objects(copy=False)
    
    if not df_transit.empty:
        df_master = df_master.merge(df_transit, on='sku', how='left')
        df_master['cantidad_en_transito'] = df_master['cantidad_en_transito'].fillna(0).infer_objects(copy=False)
    else:
        df_master['cantidad_en_transito'] = 0

    df_cons_stock = calculate_consolidated_stock(df_master[['sku', 'disponibilidad_galerias', 'disponibilidad_millennium']], df_master[['sku', 'cantidad_en_transito']])
    df_master = df_master.merge(df_cons_stock, on='sku', how='left')

    security_stock_map = {}
    for item in raw_items:
        for variant in item.get('variants', []):
            stores = variant.get('stores', [])
            if stores: security_stock_map[str(variant.get('sku'))] = stores[0].get('low_stock', 0)

    df_master['stock_seguridad_base'] = df_master['sku'].map(security_stock_map).fillna(0).infer_objects(copy=False)
    df_final = calculate_reorder_point(df_master, lead_time_days=lead_time, security_factor=security_factor)
    df_final = determine_inventory_status(df_final)

    # 5. SALIDA
    df_clean_output = sanitize_output_data(df_final)
    cols_order = ['sku', 'nombre', 'status', 'stock_total', 'reorder_point', 'qty_to_reorder', 'velocidad', 'prediccion_prophet', 'mean_demand', 'disponibilidad_galerias', 'disponibilidad_millennium', 'cantidad_en_transito', 'stock_seguridad']
    df_export = df_clean_output[[c for c in cols_order if c in df_clean_output.columns]]
    export_to_csv(df_export, str(REPORT_FILE))
    export_to_csv(df_export, str(BASE_REPORT_FILE))
    
    # 6. SINCRONIZACIÓN BD
    try:
        from src.database.db_core import SessionLocal
        from src.database.models import Product
        import uuid

        db = SessionLocal()
        status_map = dict(zip(df_export['sku'], df_export['status']))
        stock_gal_map = dict(zip(df_stock_stores['sku'], df_stock_stores.get('disponibilidad_galerias', 0)))
        stock_mil_map = dict(zip(df_stock_stores['sku'], df_stock_stores.get('disponibilidad_millennium', 0)))

        for item in raw_items:
            item_name_base = item.get('item_name', '')
            for variant in item.get('variants', []):
                sku_val = str(variant.get('sku', '')).strip()
                if not sku_val or sku_val == 'nan': continue
                
                prod = db.query(Product).filter(Product.sku == sku_val).first()
                if not prod:
                    prod = Product(id=str(uuid.uuid4()), sku=sku_val)
                    db.add(prod)
                
                prod.nombre = item_name_base 
                prod.stock_galerias = float(stock_gal_map.get(sku_val, 0.0))
                prod.stock_millennium = float(stock_mil_map.get(sku_val, 0.0))
                prod.status_prediccion = status_map.get(sku_val, 'NUEVO')

        db.commit()
        
        df_tabla_maestra = pd.read_sql(db.query(Product).statement, db.bind)
        df_tabla_maestra.to_csv(OUTPUT_DIR / "maestro_productos_unificado.csv", index=False, encoding='utf-8-sig')
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Error actualizando Tabla Maestra: {e}")
    finally:
        db.close()

    logger.info(">>> PIPELINE NOCTURNO EXITOSO <<<")


# ==============================================================================
# JOB 2: PIPELINE RÁPIDO (Sincronización Transaccional - Cada 5 min)
# Actualiza el stock en vivo con la lógica oficial del sistema.
# ==============================================================================
def run_fast_sync_pipeline():
    
    logger = get_custom_logger("motor_rapido", "orquestador_rapido.log")
    logger.info("⚡ Iniciando Sincronización Rápida de Inventario (5 min)...")

    logger.info("⚡ Iniciando Sincronización Rápida de Inventario (5 min)...")
    config = load_config()
    token = config['api'].get('loyverse_token')
    if token:
        os.environ['LOYVERSE_TOKEN'] = token

    # 1. Bajar ventas del POS a la BD Local (Mantiene auditoría financiera intacta)
    try:
        from src.data_ingestion import sync_loyverse_sales_to_local
        sync_loyverse_sales_to_local()
    except Exception as e:
        logger.error(f"Fallo sincronizando ventas de Loyverse: {e}")

    # 2. Leer el Caché INMUTABLE de la noche anterior
    if not BASE_REPORT_FILE.exists():
        logger.warning("No existe reporte_base_ml.csv. Ejecuta --nightly primero para crear la base de Inteligencia Artificial.")
        return
        
    df_ml = pd.read_csv(BASE_REPORT_FILE, encoding='utf-8-sig')
    df_ml['sku'] = df_ml['sku'].astype(str)

    # Limpiar las columnas de stock/estado del caché para no duplicarlas
    cols_to_update = ['disponibilidad_galerias', 'disponibilidad_millennium', 'cantidad_en_transito', 'stock_total', 'status', 'qty_to_reorder']
    df_ml = df_ml.drop(columns=[c for c in cols_to_update if c in df_ml.columns], errors='ignore')

    # 3. Traer el Stock VIVO directamente desde Loyverse (O(1) Exactitud)
    try:
        from src.data_ingestion import fetch_api_data, load_purchase_orders
        from src.inventory_logic import calculate_consolidated_stock, determine_inventory_status
        from src.output_manager import export_to_csv, sanitize_output_data

        # Descargas ultra-rápidas a la API
        raw_inventory = fetch_api_data('/inventory')
        raw_items = fetch_api_data('/items')
        
        # Parsear con la misma función del Nightly
        df_stock_stores = _parse_inventory_levels(raw_inventory, raw_items, config['tiendas'])
        
        # =================================================================
        # 🚀 NUEVO: INTERCEPTOR PARA MATRIZ WOOCOMMERCE
        # =================================================================
        try:
            # Guardamos la foto temporal del stock físico para cruzarla luego
            ruta_tmp_loyverse = OUTPUT_DIR / "loyverse_stock_tmp.csv"
            
            # Aseguramos de que las columnas existan antes de guardarlas para evitar KeyError
            cols_to_save = ['sku']
            if 'disponibilidad_millennium' in df_stock_stores.columns: cols_to_save.append('disponibilidad_millennium')
            if 'disponibilidad_galerias' in df_stock_stores.columns: cols_to_save.append('disponibilidad_galerias')
                
            df_stock_stores[cols_to_save].to_csv(ruta_tmp_loyverse, index=False)
            logger.info("✅ Interceptor: loyverse_stock_tmp.csv guardado temporalmente.")
        except Exception as e:
            logger.warning(f"⚠️ Error menor guardando el interceptor de Loyverse: {e}")
        # =================================================================

        # Cargar tránsito en vivo
        
        
        # --- NUEVA LÓGICA DE NEGOCIO: Cargar tránsito real desde Base de Datos SQL ---
        db = SessionLocal()
        try:
            # Consulta SQL que filtra estados y resta lo que ya llegó parcialmente
            transito_query = db.query(
                PurchaseOrderItem.product_id,
                func.sum(PurchaseOrderItem.cantidad - PurchaseOrderItem.cantidad_recibida).label('total_transito')
            ).join(
                PurchaseOrder, PurchaseOrderItem.po_id == PurchaseOrder.id
            ).filter(
                PurchaseOrder.status.in_(['Esperado', 'Pagado', 'Recibido Parcialmente'])
            ).group_by(
                PurchaseOrderItem.product_id
            ).all()

            # Mapeo de product_id a SKU (Necesitamos buscar el SKU en la base de datos)
            from src.database.models import Product
            data_transito = []
            for row in transito_query:
                prod = db.query(Product).filter_by(id=row.product_id).first()
                if prod:
                    data_transito.append({
                        'sku': str(prod.sku), 
                        'cantidad_en_transito': float(row.total_transito or 0.0)
                    })
            
            if data_transito:
                df_transit = pd.DataFrame(data_transito)
                # Agrupar por si acaso un mismo producto está en varias órdenes distintas
                df_transit = df_transit.groupby('sku', as_index=False)['cantidad_en_transito'].sum()
            else:
                df_transit = pd.DataFrame(columns=['sku', 'cantidad_en_transito'])
        finally:
            db.close()
        # ----------------------------------------------------------------------------



        # Unir datos vivos al cerebro de ML
        df_final = pd.merge(df_ml, df_stock_stores, on='sku', how='left')
        df_final = pd.merge(df_final, df_transit, on='sku', how='left')
        
        # Rellenar nulos
        for col in ['disponibilidad_galerias', 'disponibilidad_millennium', 'cantidad_en_transito']:
            if col in df_final.columns:
                df_final[col] = df_final[col].fillna(0).infer_objects(copy=False)

        # 4. Calcular Stock Total Oficial
        df_cons_stock = calculate_consolidated_stock(
            df_final[['sku', 'disponibilidad_galerias', 'disponibilidad_millennium']], 
            df_final[['sku', 'cantidad_en_transito']]
        )
        df_final = pd.merge(df_final, df_cons_stock, on='sku', how='left')

        # 5. Recalcular Necesidad de Compra
        if 'reorder_point' in df_final.columns:
            df_final['qty_to_reorder'] = (df_final['reorder_point'] - df_final['stock_total'] - df_final['cantidad_en_transito']).clip(lower=0)

        # 6. Recalcular Estado usando LA MISMA FUNCIÓN OFICIAL DEL SISTEMA
        df_final = determine_inventory_status(df_final)

        # Exportar al Dashboard (Sanitizado)
        df_clean_output = sanitize_output_data(df_final)
        export_to_csv(df_clean_output, str(REPORT_FILE))
        logger.info("✅ Reporte actualizado con stock real de Loyverse y estatus corregidos.")

        # =================================================================
        # 7. Actualizar Maestro de Productos (Stock Vivo, PRECIOS y Costos)
        # =================================================================
        maestro_file = OUTPUT_DIR / "maestro_productos_unificado.csv"
        try:
            from src.database.models import Product
            
            # 1. Traemos la foto m�s fresca de TODA la tabla Product (Precios y Costos reales)
            db = SessionLocal()
            df_db = pd.read_sql(db.query(Product).statement, db.bind)
            db.close()
            
            # Aseguramos que el SKU sea string para el cruce
            df_db['sku'] = df_db['sku'].astype(str)
            
            # 2. Preparamos el stock vivo que acabamos de bajar de Loyverse
            df_tiendas_vivo = df_stock_stores[['sku', 'disponibilidad_galerias', 'disponibilidad_millennium']].copy()
            df_tiendas_vivo['sku'] = df_tiendas_vivo['sku'].astype(str)
            df_tiendas_vivo.rename(columns={
                'disponibilidad_galerias': 'stock_galerias',
                'disponibilidad_millennium': 'stock_millennium'
            }, inplace=True)

            # 3. Cruzamos: Mantenemos todos los datos de DB, pero reemplazamos el stock con el vivo
            df_maestro_actualizado = pd.merge(df_db, df_tiendas_vivo, on='sku', how='left', suffixes=('_old', ''))
            
            # 4. Limpieza de columnas duplicadas (rellenamos nulos de la API con los de la DB y borramos old)
            for col in ['stock_galerias', 'stock_millennium']:
                if f'{col}_old' in df_maestro_actualizado.columns:
                    df_maestro_actualizado[col] = df_maestro_actualizado[col].fillna(df_maestro_actualizado[f'{col}_old'])
                    df_maestro_actualizado.drop(columns=[f'{col}_old'], inplace=True)

            # 5. Guardado at�mico del maestro totalmente refrescado
            export_to_csv(df_maestro_actualizado, str(maestro_file))
            logger.info("? Maestro refrescado con Precios, Costos oficiales de DB y Stock vivo.")
            
        except Exception as e:
            logger.error(f"?? Error reconstruyendo el Maestro (Stock y Precios): {e}", exc_info=True)
            
            
        # =================================================================
        # 8. Sincronización Omnicanal (WooCommerce y Cashea)
        # =================================================================
        
        
        # DELEGADO A "/home/night/Sistema_ERP_Intranet/src/services/database/silver/inventory_woo_cashea/sincronizador_omnicanal.py"

        # =================================================================
        # 9. 🚀 NUEVO: CONSTRUCCIÓN DE MATRIZ UNIFICADA PARA WOOCOMMERCE
        # =================================================================
        try:
            from src.woo_matrix_builder import build_woo_matrix
            logger.info("🏗️ Invocando al constructor de la Matriz Unificada de WooCommerce...")
            build_woo_matrix()
            logger.info("✅ Pipeline Fast concluido: Matriz Unificada generada con éxito.")
        except Exception as e:
            logger.error(f"❌ Fallo construyendo la matriz unificada de WooCommerce: {e}", exc_info=True)
        
        # =================================================================
        # 10. GENERAR MATRIZ DE PRECIOS STANDALONE
        # =================================================================
        try:
            logger.info("📊 Generando Matriz de Precios Standalone en segundo plano...")
            from src.database.models import Product, PricingMatrix
            db = SessionLocal()
            
            # 🛠️ Consulta inteligente: Unimos Productos con su Matriz de Precios
            query = db.query(
                Product.sku,
                Product.nombre,
                Product.costo_base,
                Product.inventario_entrante,
                PricingMatrix.volumen_m3,
                PricingMatrix.margen_deseado,
                PricingMatrix.precio_competencia
            ).outerjoin(PricingMatrix, Product.id == PricingMatrix.product_id).statement
            
            df_base_datos = pd.read_sql(query, db.bind)
            db.close()

            # Ahora sí enviamos los datos completos
            generar_y_guardar_matriz_standalone(df_base_datos, config)
            logger.info("✅ Matriz Standalone guardada exitosamente en la carpeta backup.")
        except Exception as e:
            logger.error(f"❌ Error al generar Matriz Standalone: {e}", exc_info=True)

            
    except Exception as e:
        logger.error(f"Fallo en la actualización de stock en vivo: {e}", exc_info=True)

# ==============================================================================
# ORQUESTADOR CLI
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ERP Orchestrator V8")
    parser.add_argument("--nightly", action="store_true", help="Ejecuta el entrenamiento pesado de Machine Learning (Ingesta total).")
    parser.add_argument("--fast", action="store_true", help="Ejecuta la sincronización de inventario rápida.")
    
    args = parser.parse_args()

    try:
        # Cargar entorno de forma segura
        load_config() 
        
        if args.nightly:
            run_daily_ml_pipeline()
        elif args.fast:
            run_fast_sync_pipeline()
        else:
            # Comportamiento por defecto al probar en local
            logger.info("Modo por defecto: Ejecutando Fast Sync...")
            run_fast_sync_pipeline()
    except Exception as e:
        logger.critical(f"FALLO CRÍTICO EN EL ORQUESTADOR: {e}", exc_info=True)
        sys.exit(1)