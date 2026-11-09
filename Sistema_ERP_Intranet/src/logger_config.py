#!/usr/bin/env python
# coding: utf-8
import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler # 🛡️ NUEVO IMPORT DE SEGURIDAD

# Definimos la ruta relativa al proyecto
BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"

# Crea la carpeta 'logs' dentro de tu proyecto si no existe
LOG_DIR.mkdir(parents=True, exist_ok=True)

def get_custom_logger(logger_name: str, filename: str) -> logging.Logger:
    """Fábrica universal de logs con rotación de archivos y salida a consola (Streamlit)."""
    logger = logging.getLogger(logger_name)
    
    # Evitar duplicar logs si se llama varias veces en la misma sesión
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        
        # 🛡️ 1. Escritura en Archivo Físico (BLINDADO CON ROTACIÓN)
        # Máximo 5 MB por archivo, guardando solo los últimos 3 backups.
        log_path = LOG_DIR / filename
        file_handler = RotatingFileHandler(
            log_path, 
            maxBytes=5*1024*1024, # 5 MB limite
            backupCount=3,        # Retiene 3 archivos viejos
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        # 2. Salida a Consola (Vital para que Streamlit intercepte los logs en pantalla)
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
        
        # Desactivar propagación al root logger para no duplicar
        logger.propagate = False
        
    return logger