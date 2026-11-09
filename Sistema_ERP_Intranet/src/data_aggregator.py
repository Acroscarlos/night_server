import json
import os
import sqlite3
import subprocess
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

# ==========================================
# CONFIGURACIÓN DE RUTAS ABSOLUTAS
# ==========================================
BASE_DIR = Path("/home/night/Sistema_ERP_Intranet")
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
LOG_DIR = BASE_DIR / "logs"

JSON_FINAL_PATH = OUTPUT_DIR / "tv_state.json"
JSON_TMP_PATH = OUTPUT_DIR / "tv_state.tmp"
DB_DOLAR_PATH = DATA_DIR / "historico_divisas.db"

PRICING_CONFIG = DATA_DIR / "pricing_config.json"
HISTORICO_VENTAS = DATA_DIR / "historico_ventas.csv"
REPORTE_FINAL = OUTPUT_DIR / "reporte_final.csv"

# Logs de actualización manual de precios
WOO_LOG_PATH = LOG_DIR / "woo_precios.log"
CASHEA_LOG_PATH = LOG_DIR / "cashea_precios.log"
TOP_VEND_CSV = OUTPUT_DIR / "Top_Vendedores_Woo.csv"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ==========================================
# 1. BASE DE DATOS: HISTÓRICO DE DIVISAS
# ==========================================
def init_dolar_db():
    conn = sqlite3.connect(DB_DOLAR_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS dolar_history (
            fecha TEXT PRIMARY KEY,
            oficial REAL,
            paralelo REAL,
            hora_actualizacion TEXT
        )
    ''')
    conn.commit()
    return conn

def update_dolar_history(oficial, paralelo):
    conn = init_dolar_db()
    cursor = conn.cursor()
    hoy = datetime.now().strftime("%Y-%m-%d")
    hora = datetime.now().strftime("%H:%M:%S")
    cursor.execute('''
        INSERT OR REPLACE INTO dolar_history (fecha, oficial, paralelo, hora_actualizacion)
        VALUES (?, ?, ?, ?)
    ''', (hoy, oficial, paralelo, hora))
    conn.commit()
    conn.close()

def normalize_filename(name):
    """
    Normaliza nombres para archivos: 'José Carreño' -> 'jose_carreno'
    Elimina acentos, convierte a minúsculas y cambia espacios por guiones bajos.
    """
    import unicodedata
    import re
    # Eliminar acentos y eñes
    name = unicodedata.normalize('NFD', name).encode('ascii', 'ignore').decode('utf-8')
    # Quitar caracteres especiales y pasar a minúsculas
    name = re.sub(r'[^\w\s-]', '', name).strip().lower()
    # Reemplazar espacios por guiones bajos
    return re.sub(r'[-\s]+', '_', name)

# ==========================================
# 2. FUNCIONES DE TELEMETRÍA (PINGS Y TIMESTAMPS)
# ==========================================
def ping_server(ip):
    try:
        resultado = subprocess.run(["ping", "-c", "1", "-W", "1", ip], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return "ONLINE 🟢" if resultado.returncode == 0 else "OFFLINE 🔴"
    except:
        return "OFFLINE 🔴"

def get_file_mod_time(filepath):
    if os.path.exists(filepath):
        ts = os.path.getmtime(filepath)
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %I:%M %p")
    return "Nunca"

# ==========================================
# 3. LECTURA DE DÓLAR AUTÓNOMA Y CÁLCULOS
# ==========================================
def get_dolar_and_ratios():
    oficial, paralelo = None, None
    ratio_actual = 0.0
    oficial_fallback, paralelo_fallback = 0.0, 0.0
    
    # 3.1 Leer el Ratio Actual del config (Se mantiene manual por el usuario)
    if PRICING_CONFIG.exists():
        try:
            with open(PRICING_CONFIG, 'r') as f:
                cfg = json.load(f)
                ratio_actual = cfg.get("tasa_cambio", 0.0)
                oficial_fallback = cfg.get("tasa_oficial_ref", 0.0)
                paralelo_fallback = cfg.get("tasa_paralelo_ref", 0.0)
        except Exception:
            pass # Evita crashes si el JSON está siendo editado
            
    # 3.2 Consultar Nueva API (ve.dolarapi.com) - Estructura de Lista
    try:
        resp = requests.get("https://ve.dolarapi.com/v1/dolares", timeout=5).json()
        
        # Iteramos sobre la lista para extraer los valores exactos
        for item in resp:
            if item.get("fuente") == "oficial":
                oficial = float(item.get("promedio", 0.0))
            elif item.get("fuente") == "paralelo":
                paralelo = float(item.get("promedio", 0.0))
    except Exception:
        pass # Falla silenciosa, usamos el fallback de seguridad
        
    # 3.3 Fallback (Circuito de contingencia)
    if not oficial or not paralelo:
        oficial = oficial_fallback
        paralelo = paralelo_fallback
        
    if oficial and paralelo:
        update_dolar_history(oficial, paralelo)
        
    # 3.4 Cálculos Matemáticos de Alto Nivel
    # KPI 3: Tasa de Cambio (Tasa Paralelo / Tasa Oficial) * 100
    tasa_cambio_pct = (paralelo / oficial) * 100 if oficial else 0.0
    
    # KPI 5: Diferencia (Ratio Actual - Tasa de Cambio)
    # Ej: 135.0 - 129.32 = 5.68
    diferencia = ratio_actual - tasa_cambio_pct
    
    return {
        "oficial": round(oficial, 4) if oficial else 0.0,
        "paralelo": round(paralelo, 4) if paralelo else 0.0,
        "tasa_cambio_pct": round(tasa_cambio_pct, 2),
        "ratio_actual": round(ratio_actual, 2),
        "diferencia": round(diferencia, 2)
    }

# ==========================================
# 4. LECTURA COMERCIAL (VENTAS Y CRÍTICOS)
# ==========================================
def get_commercial_data():
    top_ventas = []
    criticos_marcas = {}
    
    # 4.1 Top Ventas 30 Días
    if HISTORICO_VENTAS.exists():
        df_v = pd.read_csv(HISTORICO_VENTAS)
        df_v['created_at'] = pd.to_datetime(df_v['created_at'], utc=True)
        hace_30_dias = pd.Timestamp.now(tz='UTC') - pd.Timedelta(days=30)
        df_30d = df_v[df_v['created_at'] >= hace_30_dias]
        
        if not df_30d.empty:
            top_df = df_30d.groupby(['sku', 'item_name'])['quantity'].sum().reset_index()
            top_df = top_df.sort_values('quantity', ascending=False).head(5)
            top_ventas = top_df.to_dict('records')

    # 4.2 Top Críticos por Marca (Solo SKU y Nombre por privacidad)
    if REPORTE_FINAL.exists():
        df_r = pd.read_csv(REPORTE_FINAL)
        df_c = df_r[df_r['status'] == 'CRITICO'].copy()
        
        marcas_objetivo = ['UNI-T', 'HIKMICRO', 'RV', 'JCD', 'ZIBOO']
        for marca in marcas_objetivo:
            df_marca = df_c[df_c['nombre'].str.upper().str.contains(marca) | df_c['sku'].str.upper().str.contains(marca)]
            # Ordenamos por velocidad pero NO enviamos esa columna al JSON por seguridad
            top_marca = df_marca.sort_values('velocidad', ascending=False).head(5)
            criticos_marcas[marca] = top_marca[['sku', 'nombre']].to_dict('records')
            
    return top_ventas, criticos_marcas



# ==========================================
# 4.5 LECTURA DE TOP VENDEDORES Y PREMIOS
# ==========================================
def get_sellers_data():
    if not TOP_VEND_CSV.exists():
        return {"mes_actual": {}, "historial": []}
    
    try:
        df = pd.read_csv(TOP_VEND_CSV)
        
        # 🛡️ FILTRO DE SEGURIDAD: Excluir registros ANULADOS
        if 'Estado' in df.columns:
            df = df[df['Estado'].str.upper() != 'ANULADO'].copy() #
        
        # 1. Limpieza de Monedas y Fechas
        df['Monto Base'] = df['Monto Base'].astype(str).str.replace('$', '', regex=False).str.replace(',', '', regex=False)
        df['Monto Base'] = pd.to_numeric(df['Monto Base'], errors='coerce').fillna(0.0)
        df['Fecha Operación'] = pd.to_datetime(df['Fecha Operación'], format='%d/%m/%Y', errors='coerce')
        df = df.dropna(subset=['Fecha Operación'])
        
        # 2. Filtrar "Premio/Bono/Abono" (Solo ventas reales)
        df_ventas = df[~df['ID Orden'].astype(str).str.contains('Premio|Bono|Abono', case=False, na=False)].copy()
        
        # 3. Agrupar por Periodo y Vendedor
        df_ventas['Periodo'] = df_ventas['Fecha Operación'].dt.strftime('%Y-%m')
        resumen = df_ventas.groupby(['Periodo', 'Vendedor']).agg(
            ventas_count=('ID Orden', 'count'),
            volumen_total=('Monto Base', 'sum')
        ).reset_index()
        
        meses = sorted(resumen['Periodo'].unique(), reverse=True)
        if not meses: return {"mes_actual": {}, "historial": []}
            
        mes_actual_str = meses[0]
        historial = []
        mes_actual_data = {"periodo": mes_actual_str, "deja_pa_los_demas": [], "pez_gordo": []}
        
        for mes in meses:
            df_mes = resumen[resumen['Periodo'] == mes]
            max_ventas = df_mes['ventas_count'].max()
            max_vol = df_mes['volumen_total'].max()
            
            ganadores_cant = df_mes[df_mes['ventas_count'] == max_ventas]['Vendedor'].tolist()
            ganadores_vol = df_mes[df_mes['volumen_total'] == max_vol]['Vendedor'].tolist()
            
            if mes == mes_actual_str:
                # 🛡️ Aplicamos la normalización al nombre para la búsqueda de fotos
                mes_actual_data["deja_pa_los_demas"] = [
                    {"nombre": v, "id_foto": normalize_filename(v), "valor": int(max_ventas)} 
                    for v in ganadores_cant
                ]
                mes_actual_data["pez_gordo"] = [
                    {"nombre": v, "id_foto": normalize_filename(v), "valor": float(max_vol)} 
                    for v in ganadores_vol
                ]
            else:
                historial.append({
                    "Mes": mes,
                    "🏃 Deja pa' los demás": ", ".join(ganadores_cant) + f" ({int(max_ventas)} vnts)",
                    "🦈 Pez Gordo": ", ".join(ganadores_vol) + f" (${float(max_vol):.2f})"
                })
                
        return {"mes_actual": mes_actual_data, "historial": historial}
        
    except Exception as e:
        print(f"❌ Error procesando CSV Vendedores: {e}")
        return {"mes_actual": {}, "historial": []}

# ==========================================
# 5. CONSTRUCTOR DEL ESTADO
# ==========================================
def update_wallboard_state():
    dolar_data = get_dolar_and_ratios()
    top_ventas, criticos_marcas = get_commercial_data()
    vendedores_data = get_sellers_data()
    
    estado_pantalla = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %I:%M:%S %p"),
        "dolar": dolar_data,
        "servidores": {
            "srv_principal": ping_server("192.168.1.184"),
            "srv_rvtech": ping_server("192.168.1.242")
        },
        "jobs_time": {
            "motor_rapido": get_file_mod_time(REPORTE_FINAL),
            "motor_nocturno": get_file_mod_time(DATA_DIR / "reporte_base_ml.csv"),
            "woo_precios": get_file_mod_time(WOO_LOG_PATH),
            "cashea_precios": get_file_mod_time(CASHEA_LOG_PATH)
        },
        "top_ventas": top_ventas,
        "criticos_marcas": criticos_marcas,
        "top_vendedores_reales": vendedores_data
    }
    
    try:
        with open(JSON_TMP_PATH, 'w', encoding='utf-8') as f:
            json.dump(estado_pantalla, f, ensure_ascii=False, indent=4)
        os.replace(JSON_TMP_PATH, JSON_FINAL_PATH)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ tv_state.json actualizado con éxito.")
    except Exception as e:
        print(f"❌ Error crítico escribiendo el estado: {e}")

if __name__ == "__main__":
    update_wallboard_state()