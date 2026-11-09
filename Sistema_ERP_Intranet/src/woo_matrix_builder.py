import pandas as pd
from pathlib import Path
from datetime import datetime

# ==========================================
# CONFIGURACIÓN DE RUTAS (DINÁMICA Y MULTIPLATAFORMA)
# ==========================================
# ¿Cómo funciona? 
# __file__ sabe dónde está este script exacto (src/woo_matrix_builder.py).
# .parent sube a "src", y el segundo .parent sube a la raíz "Sistema_ERP_Intranet".
# Así no importa en qué PC o disco duro esté el proyecto, siempre encontrará la raíz.

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"

# Definimos los archivos usando el operador / (Mucho más limpio que os.path.join)
MAESTRO_PATH = OUTPUT_DIR / "maestro_productos_unificado.csv"
LOYVERSE_STOCK_PATH = OUTPUT_DIR / "loyverse_stock_tmp.csv"
WOO_MATRIX_PATH = OUTPUT_DIR / "Matriz_unificada_Woocommerce.csv"
PRICING_MATRIX_PATH = OUTPUT_DIR / "matrix_de_precios.csv" 
REPORTE_FINAL_PATH = OUTPUT_DIR / "reporte_final.csv"

# Nos aseguramos de que la carpeta output exista (por si clonan el repo vacío)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def build_woo_matrix():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚙️ Construyendo Matriz WooCommerce...")
    
    # 1. Validar que el maestro exista
    if not MAESTRO_PATH.exists():
        print(f"❌ Error: No se encontró {MAESTRO_PATH.name}")
        return

    try:
        # 2. Cargar el Maestro
        df_maestro = pd.read_csv(MAESTRO_PATH)
        
        # Filtrar solo las columnas solicitadas
        columnas_requeridas = ['sku', 'nombre', 'precio_venta', 'status_prediccion', 'inventario_entrante']
        
        # Asegurarnos de que existan en el dataframe (prevención de errores)
        columnas_presentes = [col for col in columnas_requeridas if col in df_maestro.columns]
        df_woo = df_maestro[columnas_presentes].copy()

        # 3. Transformación: Convertir 'inventario_entrante' a Si/No
        if 'inventario_entrante' in df_woo.columns:
            # Convertimos a numérico por seguridad (si hay letras, las vuelve NaN y luego 0)
            df_woo['inventario_entrante'] = pd.to_numeric(df_woo['inventario_entrante'], errors='coerce').fillna(0)
            # Aplicamos la regla lógica
            df_woo['inventario_entrante'] = df_woo['inventario_entrante'].apply(lambda x: "Si" if x >= 1 else "No")

        # 4. Cruzar con datos de Loyverse (El Interceptor)
        if LOYVERSE_STOCK_PATH.exists():  # <-- CAMBIO AQUÍ
            df_loyverse = pd.read_csv(LOYVERSE_STOCK_PATH)
            # Asegurarse de que el CSV de Loyverse tenga al menos 'sku', 'stock_millennium', 'stock_galerias'
            
            # Left join: Mantenemos todos los productos del maestro, y les pegamos el stock si existe
            df_woo = pd.merge(df_woo, df_loyverse, on='sku', how='left')
            
            # Llenar con 0 si un producto del maestro no está en esa tienda de Loyverse
            if 'stock_millennium' in df_woo.columns:
                df_woo['stock_millennium'] = df_woo['stock_millennium'].fillna(0).astype(int)
            if 'stock_galerias' in df_woo.columns:
                df_woo['stock_galerias'] = df_woo['stock_galerias'].fillna(0).astype(int)
        else:
            print("⚠️ Aviso: No se encontró loyverse_stock_tmp.csv. Se generará la matriz sin stock de tiendas físicas.")


        # ==========================================
        # 4.5 Cruzar con Matriz de Precios (Extracción de Divisas)
        # ==========================================
        if PRICING_MATRIX_PATH.exists():
            df_precios = pd.read_csv(PRICING_MATRIX_PATH)
            
            # Verificamos que las columnas existan (en minúscula como sale de pricing.py)
            if 'Precio_Divisas' in df_precios.columns and 'sku' in df_precios.columns:
                df_divisas = df_precios[['sku', 'Precio_Divisas']].copy()
                
                # Estandarización de seguridad para el Join
                df_divisas['sku'] = df_divisas['sku'].astype(str).str.strip()
                df_woo['sku'] = df_woo['sku'].astype(str).str.strip()
                
                # Inyectamos usando tu variable real (df_woo)
                df_woo = pd.merge(df_woo, df_divisas, on='sku', how='left')
                
                # Blindaje contra Nulos
                df_woo['Precio_Divisas'] = df_woo['Precio_Divisas'].fillna(0.0).round(2)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 💱 Precio_Divisas integrado exitosamente.")
            else:
                print("⚠️ Aviso: Columnas no encontradas en matrix_de_precios.csv")
        else:
            df_woo['Precio_Divisas'] = 0.0
            print("⚠️ Aviso: No se encontró matrix_de_precios.csv. Divisas en 0.0")


        # ==========================================
        # 4.7 Cruzar con Reporte Final (Inyección de Velocidad)
        # ==========================================
        if REPORTE_FINAL_PATH.exists():
            # Cargamos el reporte
            df_reporte = pd.read_csv(REPORTE_FINAL_PATH)
            
            # ROBUSTEZ: Validamos que las columnas necesarias existan
            if 'velocidad' in df_reporte.columns and 'sku' in df_reporte.columns:
                # Extraemos solo lo necesario
                df_vel = df_reporte[['sku', 'velocidad']].copy()
                
                # LIMPIEZA: Eliminamos espacios invisibles para asegurar el cruce
                df_vel['sku'] = df_vel['sku'].astype(str).str.strip()
                df_woo['sku'] = df_woo['sku'].astype(str).str.strip()
                
                # UNIÓN: Left Join para mantener todos los productos de la tienda
                df_woo = pd.merge(df_woo, df_vel, on='sku', how='left')
                
                # 🛡️ REPRESENTACIÓN DE LENTITUD/FALTA DE INFO: 
                # Si un SKU no tiene velocidad, asignamos 0.
                df_woo['velocidad'] = df_woo['velocidad'].fillna(0)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 🚀 Velocidad integrada (Default: 0 para SKUs sin datos).")
            else:
                print("⚠️ Aviso: Columnas no encontradas en reporte_final.csv. Se usará 0 por defecto.")
                df_woo['velocidad'] = 0
        else:
            # Fallback: Si el archivo no existe, marcamos todo como 0
            df_woo['velocidad'] = 0
            print("⚠️ Aviso: No se encontró reporte_final.csv. Columna 'velocidad' inicializada en 0.")




        # 5. Guardar el resultado final
        df_woo.to_csv(WOO_MATRIX_PATH, index=False, encoding='utf-8')
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Matriz_unificada_Woocommerce.csv generada con éxito.")

    except Exception as e:
        print(f"❌ Error crítico procesando la matriz: {e}")

if __name__ == "__main__":
    build_woo_matrix()