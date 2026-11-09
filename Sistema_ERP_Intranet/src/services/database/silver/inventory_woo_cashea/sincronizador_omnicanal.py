#!/usr/bin/env python
# coding: utf-8

import os
import sys
import json
import logging
import pandas as pd
from pathlib import Path

# Configuración de rutas estáticas absolutas
BASE_DIR = Path("/home/night/Sistema_ERP_Intranet")
OUTPUT_DIR = BASE_DIR / "output"
CONFIG_FILE = BASE_DIR / "config" / "config.json"
STOCK_TMP_FILE = OUTPUT_DIR / "loyverse_stock_tmp.csv"
MATRIZ_WOO_FILE = OUTPUT_DIR / "Matriz_unificada_Woocommerce.csv"

# Agregar BASE_DIR al path para importar módulos de src
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

# Configurar logger
logging.basicConfig(
    filename=BASE_DIR / 'logs' / 'omnicanal_standalone.log',
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("Omnicanal_Standalone")

def cargar_config():
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def ejecutar_sincronizacion_desacoplada():
    
    logger.info("Iniciando sincronización omnicanal independiente...")

    if not STOCK_TMP_FILE.exists() or not MATRIZ_WOO_FILE.exists():
        logger.error("Archivos base (stock_tmp o Matriz_unificada) no encontrados.")
        sys.exit(1)

    try:
        config = cargar_config()
        # Leer las tablas
        df_stock = pd.read_csv(STOCK_TMP_FILE, dtype={'sku': str})
        df_matriz = pd.read_csv(MATRIZ_WOO_FILE, dtype={'sku': str})

        # 1. EVITAR COLISIÓN: Borramos el stock desactualizado de la matriz antes de unir
        cols_a_borrar = ['disponibilidad_galerias', 'disponibilidad_millennium', 'stock_total']
        df_matriz = df_matriz.drop(columns=[c for c in cols_a_borrar if c in df_matriz.columns], errors='ignore')

        # 2. Unir por SKU (ahora el stock vivo de df_stock mantendrá sus nombres limpios)
        df_unificado = pd.merge(df_matriz, df_stock, on='sku', how='inner')

        # 3. Cálculo de stock físico base para TODOS sin filtros
        galerias = df_unificado.get('disponibilidad_galerias', pd.Series(0, index=df_unificado.index)).fillna(0)
        millennium = df_unificado.get('disponibilidad_millennium', pd.Series(0, index=df_unificado.index)).fillna(0)
        df_unificado['stock_total'] = galerias + millennium

        # Todo el inventario pasa directo, siendo fiel a Loyverse
        df_filtrado = df_unificado.copy()
        logger.info(f"Procesando el catálogo completo. SKUs totales: {len(df_filtrado)}")

        # 4. Columnas obligatorias de seguridad para Woo y Cashea
        if 'cantidad_en_transito' not in df_filtrado.columns:
            df_filtrado['cantidad_en_transito'] = 0
            
        # Rescate de nombres (fundamental para que el script Woo_inventario no bloquee por marca)
        if 'nombre' not in df_filtrado.columns:
            try:
                ruta_maestro = OUTPUT_DIR / "maestro_productos_unificado.csv"
                df_maestro = pd.read_csv(ruta_maestro, dtype={'sku': str})
                
                # Cruzamos para traer solo la columna nombre real
                df_filtrado = pd.merge(df_filtrado, df_maestro[['sku', 'nombre']], on='sku', how='left')
                df_filtrado['nombre'] = df_filtrado['nombre'].fillna("GENERICO Sin Nombre")
            except Exception as e:
                logger.error(f"No se pudo cargar el maestro de productos: {e}")
                df_filtrado['nombre'] = "GENERICO Sin Nombre"

        # Guardar CSV de auditoría final
        ruta_auditoria = OUTPUT_DIR / "df_inventario_omnicanal.csv"
        df_filtrado.to_csv(ruta_auditoria, index=False, encoding='utf-8-sig')
        logger.info(f"Archivo de auditoría guardado exitosamente en: {ruta_auditoria}")

        # Importar y enviar a WooCommerce
        from src.inventario_Auto.Woo_inventario import ejecutar_sincronizacion_woo
        logger.info("Enviando matriz procesada a WooCommerce...")
        ejecutar_sincronizacion_woo(config, df_filtrado)

        from src.inventario_Auto.Cashea_inventario import ejecutar_sincronizacion_cashea
        ejecutar_sincronizacion_cashea(config, df_filtrado)

 

    except Exception as e:
        logger.critical(f"Fallo en la sincronización desacoplada: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    ejecutar_sincronizacion_desacoplada()