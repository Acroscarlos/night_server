#!/usr/bin/env python
# coding: utf-8

import json
import pandas as pd
import numpy as np
from datetime import datetime, date
from uuid import UUID
from decimal import Decimal

class SafeJSONEncoder(json.JSONEncoder):
    """
    Codificador Universal de grado Enterprise.
    Intercepta tipos de datos complejos de SQLAlchemy, Pandas y Numpy,
    y los traduce de forma segura a JSON estándar.
    """
    def default(self, obj):
        # 1. Fechas y Tiempos -> Formato ISO 8601 (Estándar de APIs)
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        
        # 2. Identificadores Únicos (UUID) -> String
        if isinstance(obj, UUID):
            return str(obj)
        
        # 3. Tipos Financieros de Alta Precisión -> Float
        if isinstance(obj, Decimal):
            return float(obj)
            
        # 4. Escudo Anti-Nulos de Pandas (np.nan, pd.NaT) -> null (None en Python)
        # Esto evita que JSON colapse con valores vacíos de DataFrames
        if pd.isna(obj):
            return None
            
        # 5. Tipos Numéricos Nativos de C/Numpy -> Tipos Nativos de Python
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        
        # 6. Arrays de Numpy -> Listas de Python
        if isinstance(obj, np.ndarray):
            return obj.tolist()

        # Si el objeto no es ninguno de los anteriores, que actúe el comportamiento por defecto
        return super().default(obj)