#!/usr/bin/env python
# coding: utf-8

# In[ ]:


#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
from requests.auth import HTTPDigestAuth
import pymysql
import json
import logging
import uuid
from datetime import datetime, timedelta

# ==========================================
# CONFIGURACIÓN Y CREDENCIALES
# ==========================================
DB_HOST = "127.0.0.1"
DB_USER = "hikvision_ingestion"
DB_PASS = "TJgCCS11"
DB_NAME = "control_acceso_db"

HIK_IP = "192.168.1.51"
HIK_USER = "admin"
HIK_PASS = "TJgCCS11"

HIK_URL = f"http://{HIK_IP}/ISAPI/AccessControl/AcsEvent?format=json"

# Guardar el log dentro del directorio del proyecto del usuario 'night'
LOG_FILE = "/home/night/Sistema_Hikvision_Empleados/src/hikvision_bronze_ingestion.log"
OVERLAP_MINUTES = 5
MAX_RESULTS_PER_PAGE = 50

# Diccionario de métodos de autenticación detectados
DICCIONARIO_METODOS = {
    "face": "Reconocimiento Facial",
    "card": "Tarjeta RFID",
    "fp": "Huella Dactilar",
    "pw": "PIN / Contraseña",
    "password": "PIN / Contraseña",
    "faceAndCard": "Rostro + Tarjeta",
    "faceAndFp": "Rostro + Huella",
    "faceOrCard": "Rostro o Tarjeta",
    "faceOrFpOrCardOrPw": "Multi-Método (Rostro/Huella/Tarjeta/PIN)",
    "": "Evento de Sistema"
}

# ==========================================
# CONFIGURACIÓN DE TELEMETRÍA (LOGGING)
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def get_db_connection():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor
    )

def get_time_window(connection):
    """
    Calcula la ventana temporal usando lotes solapantes.
    Busca el último registro ingresado y resta OVERLAP_MINUTES.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT MAX(event_time) as last_event FROM Hikvision_ingesta_bronze")
            result = cursor.fetchone()
            
            end_time_dt = datetime.now()
            
            if result and result['last_event']:
                start_time_dt = result['last_event'] - timedelta(minutes=OVERLAP_MINUTES)
            else:
                start_time_dt = end_time_dt - timedelta(days=7) # Fallback a 7 días en primera ejecución
                
            # Formateo requerido por ISAPI: YYYY-MM-DDThh:mm:ss-04:00
            start_time_str = start_time_dt.strftime('%Y-%m-%dT%H:%M:%S-04:00')
            end_time_str = end_time_dt.strftime('%Y-%m-%dT%H:%M:%S-04:00')
            
            return start_time_str, end_time_str
    except Exception as e:
        logger.error(f"Error calculando ventana temporal: {e}")
        raise

def extract_and_load():
    logger.info("Iniciando motor de ingesta Bronze de Hikvision.")
    
    conn = None
    try:
        conn = get_db_connection()
        start_time, end_time = get_time_window(conn)
        logger.info(f"Ventana de extracción: Desde {start_time} Hasta {end_time}")
        
        headers = {'Content-Type': 'application/json'}
        search_position = 0
        
        total_fetched = 0
        total_inserted = 0
        total_ignored = 0
        
        # Generar un UUID único para toda esta transacción de extracción
        current_search_id = str(uuid.uuid4())
        
        # Paginación orientada a eficiencia de memoria RAM
        while True:
            payload = {
                "AcsEventCond": {
                    "searchID": current_search_id,
                    "searchResultPosition": search_position,
                    "maxResults": MAX_RESULTS_PER_PAGE,
                    "major": 0,
                    "minor": 0,
                    "startTime": start_time,
                    "endTime": end_time
                }
            }
            
            response = requests.post(
                HIK_URL, 
                data=json.dumps(payload), 
                headers=headers, 
                auth=HTTPDigestAuth(HIK_USER, HIK_PASS),
                timeout=15
            )
            response.raise_for_status()
            
            data = response.json()
            info_eventos = data.get("AcsEvent", {})
            eventos_api = info_eventos.get("InfoList", [])
            num_of_matches = info_eventos.get("numOfMatches", 0)
            
            if search_position == 0:
                logger.info(f"El dispositivo reporta {info_eventos.get('totalMatches', 0)} registros en la ventana temporal.")

            if num_of_matches == 0 or not eventos_api:
                break
                
            total_fetched += num_of_matches
            
            # Preparar datos y mecanismo de seguridad para validación estricta
            batch_data = []
            seriales_en_lote = []
            
            for ev in eventos_api:
                raw_time = ev.get("time", "")
                # Truncar la zona horaria para insertar limpiamente en DATETIME de MySQL
                parsed_time = raw_time[:19].replace('T', ' ') 
                
                tipo_autenticacion = str(ev.get("currentVerifyMode", ""))
                metodo_traducido = DICCIONARIO_METODOS.get(tipo_autenticacion, f"Otro ({tipo_autenticacion})")
                
                serial_no = ev.get("serialNo", 0)
                seriales_en_lote.append(serial_no)
                
                batch_data.append({
                    "serial": serial_no,
                    "tupla": (
                        serial_no,
                        ev.get("employeeNoString", "N/A"),
                        parsed_time,
                        metodo_traducido,
                        json.dumps(ev)
                    )
                })
            
            # Consultar a la BD cuáles de estos seriales ya existen (Solapados)
            seriales_existentes = set()
            if seriales_en_lote:
                format_strings = ','.join(['%s'] * len(seriales_en_lote))
                check_query = f"SELECT event_serial_no FROM Hikvision_ingesta_bronze WHERE event_serial_no IN ({format_strings})"
                with conn.cursor() as cursor:
                    cursor.execute(check_query, tuple(seriales_en_lote))
                    resultados = cursor.fetchall()
                    seriales_existentes = {fila['event_serial_no'] for fila in resultados}

            # Separar explícitamente registros nuevos de solapados
            registros_nuevos = []
            seriales_nuevos_log = []
            seriales_ignorados_log = []
            
            for item in batch_data:
                if item["serial"] in seriales_existentes:
                    seriales_ignorados_log.append(item["serial"])
                else:
                    registros_nuevos.append(item["tupla"])
                    seriales_nuevos_log.append(item["serial"])
            
            # Ejecutar Upsert seguro solo con los verdaderamente nuevos
            batch_inserted = 0
            if registros_nuevos:
                insert_query = """
                    INSERT IGNORE INTO Hikvision_ingesta_bronze 
                    (event_serial_no, employee_id, event_time, verify_mode, raw_payload)
                    VALUES (%s, %s, %s, %s, %s)
                """
                with conn.cursor() as cursor:
                    batch_inserted = cursor.executemany(insert_query, registros_nuevos)
                    conn.commit()
            
            batch_ignored = len(seriales_ignorados_log)
            
            # Telemetría a prueba de errores con visibilidad total
            logger.info(f"Página procesada - Nuevos ({batch_inserted}): {seriales_nuevos_log if seriales_nuevos_log else 'Ninguno'} | Solapados ({batch_ignored}): {seriales_ignorados_log if seriales_ignorados_log else 'Ninguno'}")
            
            total_inserted += batch_inserted
            total_ignored += batch_ignored
            
            search_position += num_of_matches
            
            # Medida de seguridad
            if search_position >= info_eventos.get("totalMatches", 0):
                break

        logger.info(f"Ingesta finalizada. Extraídos: {total_fetched} | Insertados: {total_inserted} | Ignorados (Duplicados): {total_ignored}")

    except requests.exceptions.RequestException as req_err:
        logger.error(f"Error de comunicación con el biométrico: {req_err}")
    except pymysql.MySQLError as db_err:
        logger.error(f"Error en base de datos MySQL: {db_err}")
    except Exception as e:
        logger.error(f"Error inesperado en el proceso de ingesta: {e}")
    finally:
        if conn and conn.open:
            conn.close()

if __name__ == "__main__":
    extract_and_load()

