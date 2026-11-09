import os
from pathlib import Path
from datetime import datetime


DIRECTORIO_ACTUAL = Path(__file__).resolve().parent
BASE_DIR = DIRECTORIO_ACTUAL / "src"
OUTPUT_DIR = DIRECTORIO_ACTUAL / "CONTEXTO_NOTEBOOKLM"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


V = "_v" + "6"
# 2. Diccionario de Carpetas y sus Relaciones Arquitectónicas (Para inyectar a la IA)
ESTRUCTURA = {
    "1_SRC_RAIZ" + V  : {
        "path": BASE_DIR,
        "contexto": "CAPA: RAÍZ (ROOT). Contiene utilidades globales y puntos de entrada base. Se comunica con todas las demás capas."
    },
    "2_SRC_SERVICES"  + V : {
        "path": BASE_DIR / "services",
        "contexto": "CAPA: SERVICIOS EXTERNOS. Contiene integraciones con APIs (ej. Loyverse). ES LLAMADA POR la capa de 'views' para sincronizar datos con la nube."
    },
    "3_SRC_DATABASE"  + V : {
        "path": BASE_DIR / "database",
        "contexto": "CAPA: BASE DE DATOS Y ORM (MODELO). Contiene los modelos de SQLAlchemy y la configuración SQLite. ES EL NÚCLEO. Es importada y utilizada por 'views', 'services' y 'dashboard'."
    },
    "4_SRC_DASHBOARD"  + V : {
        "path": BASE_DIR / "dashboard",
        "contexto": "CAPA: ORQUESTADOR FRONTEND. Contiene el main.py de Streamlit, la autenticación (auth.py) y carga de datos. LLAMA a los 'components' y renderiza las 'views'."
    },
    "5_SRC_COMPONENTS"  + V : {
        "path": BASE_DIR / "dashboard" / "components",
        "contexto": "CAPA: COMPONENTES UI. Contiene widgets reutilizables de Streamlit (ej. menús laterales, tarjetas). SON LLAMADOS POR el 'dashboard' y las 'views'."
    },
    "6_SRC_VIEWS"  + V : {
        "path": BASE_DIR / "dashboard" / "views",
        "contexto": "CAPA: VISTAS Y LÓGICA DE NEGOCIO (CONTROLADOR). Contiene la lógica profunda de Finanzas, Inventario, Compras, etc. IMPORTA los modelos de 'database' y LLAMA a 'services' para ejecutar operaciones ACID."
    },

    "7_SRC_AUTO" + V : {
        "path": BASE_DIR / "inventario_Auto",
        "contexto": "CAPA: VISTAS Y LÓGICA DE NEGOCIO (CONTROLADOR). Contiene la lógica profunda de Finanzas, Inventario, Compras, etc. IMPORTA los modelos de 'database' y LLAMA a 'services' para ejecutar operaciones ACID."
    },

    "8_SRC_UTILS" + V : {
        "path": BASE_DIR / "utils",
        "contexto": "CAPA: VISTAS Y LÓGICA DE NEGOCIO (CONTROLADOR). Contiene la lógica profunda de Finanzas, Inventario, Compras, etc. IMPORTA los modelos de 'database' y LLAMA a 'services' para ejecutar operaciones ACID."
    }
    
    
}

def compilar_contexto():
    print(f"🚀 Iniciando extracción de código para NotebookLM...\n")
    
    for nombre_archivo, info in ESTRUCTURA.items():
        carpeta = info["path"]
        contexto_ia = info["contexto"]
        
        if not carpeta.exists():
            print(f"⚠️ La carpeta no existe, omitiendo: {carpeta}")
            continue

        # Buscar solo archivos .py directamente en esta carpeta (no subcarpetas)
        archivos_py = [f for f in carpeta.iterdir() if f.is_file() and f.suffix == '.py']
        
        if not archivos_py:
            print(f"⏩ No hay archivos .py en {carpeta.name}, omitiendo.")
            continue

        ruta_salida = OUTPUT_DIR / f"{nombre_archivo}.txt"
        
        with open(ruta_salida, "w", encoding="utf-8") as f_out:
            # --- INYECCIÓN DE CONTEXTO PARA LA IA ---
            f_out.write("="*80 + "\n")
            f_out.write(f"DOCUMENTO DE CONTEXTO PARA IA: {nombre_archivo}\n")
            f_out.write(f"FECHA DE EXTRACCIÓN: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f_out.write(f"RELACIÓN ARQUITECTÓNICA: {contexto_ia}\n")
            f_out.write("="*80 + "\n\n")
            
            # --- CONSOLIDACIÓN DE CÓDIGO ---
            for archivo in archivos_py:
                f_out.write(f"\n{'/'*80}\n")
                f_out.write(f"// INICIO DEL ARCHIVO: {archivo.name} \n")
                f_out.write(f"{'/'*80}\n\n")
                
                try:
                    with open(archivo, "r", encoding="utf-8") as f_in:
                        f_out.write(f_in.read() + "\n")
                except Exception as e:
                    f_out.write(f"# ERROR LEYENDO ARCHIVO: {e}\n")
                
                f_out.write(f"\n// FIN DEL ARCHIVO: {archivo.name}\n")
        
        print(f"✅ Creado: {nombre_archivo}.txt (Contiene {len(archivos_py)} archivos)")

    print(f"\n🎉 ¡Proceso terminado! Los archivos están listos en: {OUTPUT_DIR}")

if __name__ == "__main__":
    compilar_contexto()