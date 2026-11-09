#!/usr/bin/env python
# coding: utf-8
import os
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool


# Definir la ruta relativa hacia el directorio 'data'
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"

# Asegurar que el directorio de datos existe
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Ruta del archivo SQLite
DB_PATH = DATA_DIR / "erp_database.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

# Configurar el Engine con Timeout para evitar bloqueos
engine = create_engine(
    DATABASE_URL,
    connect_args={
        "check_same_thread": False,
        "timeout": 30  # 🛡️ Aumentado a 30s para mayor tolerancia a la concurrencia
    },
    poolclass=NullPool, # 🛡️ CRÍTICO: Fuerza el cierre real del archivo SQLite al terminar la transacción
    echo=False
)

# Inyectar PRAGMAs para concurrencia (Write-Ahead Logging)
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute("PRAGMA busy_timeout=20000;") # 20 segundos de espera interna a nivel BD
    cursor.execute("PRAGMA foreign_keys=ON;") # 🛡️ CRÍTICO: Fuerza la integridad de Foreign Keys en SQLite
    cursor.close()

# Configurar el creador de sesiones
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Clase base declarativa para nuestros modelos ORM
Base = declarative_base()

def get_db():
    """Generador de sesiones para inyección de dependencias seguras (Patrón Unit of Work)."""
    db = SessionLocal()
    try:
        yield db
    except Exception as e:
        db.rollback() # 🛡️ PREVENCIÓN DE CORRUPCIÓN: Revierte la BD si ocurre un error
        raise e
    finally:
        db.close()

def init_db():
    """Crea las tablas en la base de datos si no existen."""
    import src.database.models  # Evitar importaciones circulares
    Base.metadata.create_all(bind=engine)