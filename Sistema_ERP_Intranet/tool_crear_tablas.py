#!/usr/bin/env python
# coding: utf-8

from src.database.db_core import engine, Base
# Importamos los modelos para que SQLAlchemy los detecte
import src.database.models 

def main():
    print("Sincronizando modelos con la base de datos SQLite...")

    # Esta instrucción crea todas las tablas que no existan previamente.
    # (No borra ni daña los datos que ya tienes, solo agrega lo nuevo)
    Base.metadata.create_all(bind=engine)

    print("✅ ¡Nuevas tablas creadas con éxito! Ya puedes usar el ERP.")

if __name__ == "__main__":
    main()