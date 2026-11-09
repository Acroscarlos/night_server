#!/usr/bin/env python
# coding: utf-8

# In[ ]:


import pandas as pd
import os
import json

def ejecutar_fase1_ingesta(ruta_origen, ruta_config):
    """
    Fase 1: Módulo de Ingesta y Filtrado Base.
    Valida el archivo, descarta el ruido (CRITICO) y retiene candidatos viables.
    """
    print("--- INICIANDO FASE 1: INGESTA Y FILTRADO ---")

    
    try:
        with open(ruta_config, 'r') as f:
            config_completo = json.load(f)
            config_promo = config_completo.get("Promo_Woo", {})
            print("Configuración 'Promo_Woo' cargada exitosamente del JSON.")
    except Exception as e:
        print(f"Error leyendo config.json, usando valores por defecto. Detalle: {e}")
        config_promo = {}

    # Extraemos el umbral con un valor de respaldo (fallback) por seguridad
    tope_precio = config_promo.get("precion_umbral", 2700)
    # -------------------------------------------------

    
    # 1. Validación de existencia del archivo
    if not os.path.exists(ruta_origen):
        print(f"ERROR CRÍTICO: No se encontró el archivo en la ruta:\n{ruta_origen}")
        return None
    
    try:
        # 2. Lectura del archivo origen
        df_completo = pd.read_csv(ruta_origen)
        total_inicial = len(df_completo)
        print(f"Archivo leído correctamente. Total de registros iniciales: {total_inicial}")
        
        # Validar que la columna clave exista
        if 'status_prediccion' not in df_completo.columns:
            print("ERROR: El archivo no contiene la columna 'status_prediccion'.")
            return None
            
        # Limpieza de seguridad: quitar espacios en blanco y forzar mayúsculas
        df_completo['status_prediccion'] = df_completo['status_prediccion'].astype(str).str.strip().str.upper()
        
        # 3. Filtrado por reglas de negocio
        estatus_validos = config_promo.get("estatus_permitidos", ["OPTIMO", "SOBRESTOCK", "REORDER"])

        # Aplicar el filtro (conservando solo los válidos)
        df_filtrado_sin_filtro = df_completo[df_completo['status_prediccion'].isin(estatus_validos)].copy()
        
        df_filtrado = df_filtrado_sin_filtro[df_filtrado_sin_filtro['precio_venta'] <= tope_precio].copy()


        # 5. Regla de negocio: Exclusión de marcas (MAP - Minimum Advertised Price)
        marcas_bloqueadas = config_promo.get("marcas_excluidas", [])
        if marcas_bloqueadas:
            marcas_bloqueadas_upper = [m.upper() for m in marcas_bloqueadas]
            # Extraemos la marca asumiendo que es la primera palabra del nombre
            df_filtrado['Marca_temp'] = df_filtrado['nombre'].astype(str).str.split().str[0].str.upper()
            df_filtrado = df_filtrado[~df_filtrado['Marca_temp'].isin(marcas_bloqueadas_upper)].copy()
            df_filtrado.drop(columns=['Marca_temp'], inplace=True)
            

        
        # Cálculos de auditoría
        total_final = len(df_filtrado)
        descartados = total_inicial - total_final
        criticos = len(df_completo[df_completo['status_prediccion'] == "CRITICO"])
        
        print(f"Registros descartados (incluyendo {criticos} CRITICOS): {descartados}")
        print(f"Candidatos viables retenidos: {total_final}\n")
        
        print("Distribución de la población viable:")
        print(df_filtrado['status_prediccion'].value_counts().to_string())
        print("-" * 45)
        
        return df_filtrado, config_promo

    except Exception as e:
        print(f"ERROR durante la ingesta de datos: {e}")
        return None



# In[ ]:


def ejecutar_fase2_muestreo(df_entrada, config_promo, fechas):
    
    """
    Fase 2: Módulo de Muestreo y Selección Aleatoria.
    Aplica las cuotas de negocio y marca a los ganadores con una 'x'.
    """
    print("--- INICIANDO FASE 2: MUESTREO Y SELECCIÓN ALEATORIA ---")
    
    # Trabajar sobre una copia para no alterar el dataframe original accidentalmente
    df = df_entrada.copy()
    
    # 1. Crear la nueva columna en blanco para todos los registros
    df['seleccionado_promo'] = ''
    
    # 2. Separar la población por estatus
    poblacion_optimo = df[df['status_prediccion'] == 'OPTIMO']
    poblacion_sobrestock = df[df['status_prediccion'] == 'SOBRESTOCK']
    poblacion_reorder = df[df['status_prediccion'] == 'REORDER']

    cuota_optimo = config_promo.get("max_item_optimo", 10)
    cuota_sobrestock = config_promo.get("max_item_sobrestock", 20)

    
    if fechas.get('hay_flash'):
        cuota_reorder = config_promo.get("max_item_reorder", 5)
    else:
        cuota_reorder = 0
    

    # 3. Extraer muestras aleatorias (con límite de seguridad anti-colapso)
    # pd.DataFrame.sample() garantiza que la selección sea verdaderamente al azar en cada ejecución
    muestra_optimo = poblacion_optimo.sample(n=min(cuota_optimo, len(poblacion_optimo)))
    muestra_sobrestock = poblacion_sobrestock.sample(n=min(cuota_sobrestock, len(poblacion_sobrestock)))
    muestra_reorder = poblacion_reorder.sample(n=min(cuota_reorder, len(poblacion_reorder)))
    
    # 4. Consolidar los índices (filas exactas) de todos los ganadores
    import pandas as pd
    indices_ganadores = pd.concat([muestra_optimo, muestra_sobrestock, muestra_reorder]).index
    
    # 5. Marcaje oficial
    df.loc[indices_ganadores, 'seleccionado_promo'] = 'x'
    
    # Auditoría de resultados
    total_ganadores = len(indices_ganadores)
    print(f"Sorteo finalizado. Ítems marcados oficialmente con 'x': {total_ganadores}\n")
    print("Distribución de la selección:")
    print(f"   - OPTIMO:     {len(muestra_optimo)} ítems (Cuota: 10)")
    print(f"   - SOBRESTOCK: {len(muestra_sobrestock)} ítems (Cuota: 20)")
    print(f"   - REORDER:    {len(muestra_reorder)} ítems (Cuota: max 5)")
    print("-" * 55)
    
    return df


# In[ ]:


import datetime
import calendar


def ejecutar_fase3_motor_fechas(config_promo, fecha_referencia=None ):
    """
    Fase 3: Motor de Calendario y Fechas.
    Proyecta el próximo Lunes a Sabado y detecta interrupciones por quincena (15) o fin de mes.
    """
    print("--- INICIANDO FASE 3: MOTOR DE CALENDARIO ---")
    
    # Si no se pasa una fecha, usamos la fecha y hora actual del servidor
    if fecha_referencia is None:
        fecha_referencia = datetime.datetime.now()
        
    print(f" Fecha base de ejecución: {fecha_referencia.strftime('%A, %d/%m/%Y')}")
    
    # 1. Calcular el próximo Lunes
    dias_para_lunes = 0 - fecha_referencia.weekday()
    if dias_para_lunes <= 0:
        dias_para_lunes += 7
    
    proximo_lunes = fecha_referencia + datetime.timedelta(days=dias_para_lunes)
    proximo_lunes = proximo_lunes.replace(hour=0, minute=0, second=0, microsecond=0)
    
    proximo_sabado = proximo_lunes + datetime.timedelta(days=5)
    proximo_sabado = proximo_sabado.replace(hour=23, minute=59, second=59, microsecond=0)

    print(f"Semana objetivo: Del Lunes {proximo_lunes.strftime('%d/%m/%Y')} al Sabado {proximo_sabado.strftime('%d/%m/%Y')}")

    # 2. Escanear la semana buscando día 15 o fin de mes
    dia_flash = None
    interruptor_flash = config_promo.get("interruptor_master_flash", True)
    if interruptor_flash:
        
        for i in range(6):
            dia_actual = proximo_lunes + datetime.timedelta(days=i)
            ultimo_dia_mes = calendar.monthrange(dia_actual.year, dia_actual.month)[1]
            
            if dia_actual.day == 15 or dia_actual.day == ultimo_dia_mes:
                dia_flash = dia_actual
                break

    # 3. Construir el diccionario de fechas con formato WooCommerce
    formato_woo = "%Y-%m-%d %H:%M:%S"
    
    fechas_promo = {
        'hay_flash': False,
        'fecha_inicio': proximo_lunes.strftime(formato_woo),
        'fecha_fin': proximo_sabado.strftime(formato_woo),
        'fecha_inicio_flash': None,
        'fecha_fin_flash': None,
        'fecha_inicio_continuacion': None,
        'fecha_fin_continuacion': None
    }

    if dia_flash:
        print(f"\n¡EVENTO FLASH DETECTADO! Fecha: {dia_flash.strftime('%A, %d/%m/%Y')}")
        fechas_promo['hay_flash'] = True
        
        # Bloque Flash (El día exacto)
        fechas_promo['fecha_inicio_flash'] = dia_flash.replace(hour=0, minute=0, second=0).strftime(formato_woo)
        fechas_promo['fecha_fin_flash'] = dia_flash.replace(hour=23, minute=59, second=59).strftime(formato_woo)
        
        # Bloque 1 (Pre-Flash)
        if dia_flash.date() > proximo_lunes.date():
            dia_anterior = dia_flash - datetime.timedelta(days=1)
            fechas_promo['fecha_fin'] = dia_anterior.replace(hour=23, minute=59, second=59).strftime(formato_woo)
            print(f"   [Bloque 1 Estándar] {fechas_promo['fecha_inicio']} ---> {fechas_promo['fecha_fin']}")
        else:
            # Seguro lógico: Si el flash es el Lunes, no hay bloque pre-flash
            fechas_promo['fecha_inicio'] = None
            fechas_promo['fecha_fin'] = None
            print(f"   [Bloque 1 Estándar] Anulado (El flash inicia el Lunes)")
            
        print(f"   [Bloque FLASH]      {fechas_promo['fecha_inicio_flash']} ---> {fechas_promo['fecha_fin_flash']}")
            
        # Bloque 2 (Post-Flash)
        if dia_flash.date() < proximo_sabado.date():
            dia_siguiente = dia_flash + datetime.timedelta(days=1)
            fechas_promo['fecha_inicio_continuacion'] = dia_siguiente.replace(hour=0, minute=0, second=0).strftime(formato_woo)
            fechas_promo['fecha_fin_continuacion'] = proximo_sabado.strftime(formato_woo)
            print(f"   [Bloque 2 Estándar] {fechas_promo['fecha_inicio_continuacion']} ---> {fechas_promo['fecha_fin_continuacion']}")
        else:
            # Seguro lógico: Si el flash es el Sabado, no hay continuación
            print(f"   [Bloque 2 Estándar] Anulado (El flash finaliza la semana)")

    else:
        print("\nSemana estándar sin eventos flash.")
        print(f"   [Bloque Único Estándar] {fechas_promo['fecha_inicio']} ---> {fechas_promo['fecha_fin']}")
        
    print("-" * 45)
    return fechas_promo


# In[ ]:


import pandas as pd
import numpy as np

def ejecutar_fase4_calculos(df_entrada, fechas, config_promo):
    """
    Fase 4: Cálculo de Precios y Asignación Dinámica.
    Aplica los descuentos matemáticos y estructura las columnas de fechas de WooCommerce.
    """
    print("--- INICIANDO FASE 4: CÁLCULOS FINANCIEROS Y FECHAS ---")
    
    if df_entrada is None or df_entrada.empty:
        print("ERROR: El DataFrame de entrada está vacío.")
        return None
        
    df_fase4 = df_entrada.copy()
    
    # 1. Crear columnas inicializadas con el tipo de dato correcto
    df_fase4['precios_promo_woo'] = np.nan
    df_fase4['precio_flash'] = np.nan
    
    columnas_fechas = [
        'fecha_inicio', 'fecha_fin', 
        'fecha_inicio_flash', 'fecha_fin_flash', 
        'fecha_inicio_continuacion', 'fecha_fin_continuacion'
    ]
    for col in columnas_fechas:
        df_fase4[col] = ""  # Inicializamos como texto vacío en lugar de float
        
    # Máscara principal
    mask_seleccionados = df_fase4['seleccionado_promo'] == 'x'
    mask_optimo = mask_seleccionados & (df_fase4['status_prediccion'] == 'OPTIMO')
    mask_sobrestock = mask_seleccionados & (df_fase4['status_prediccion'] == 'SOBRESTOCK')
    mask_reorder = mask_seleccionados & (df_fase4['status_prediccion'] == 'REORDER')

    # 2. Cálculos de Precios Estándar
    desc_optimo_est = 1 - (config_promo.get("optimo_estandar_discount", 3) / 100)
    desc_sobrestock_est = 1 - (config_promo.get("sobrestock_estandar_discount", 6) / 100)
    desc_reorder_est = 1 - (config_promo.get("reorder_estandar_discount", 0) / 100)

    df_fase4.loc[mask_optimo, 'precios_promo_woo'] = df_fase4.loc[mask_optimo, 'precio_venta'] * desc_optimo_est
    df_fase4.loc[mask_sobrestock, 'precios_promo_woo'] = df_fase4.loc[mask_sobrestock, 'precio_venta'] * desc_sobrestock_est
    df_fase4.loc[mask_reorder, 'precios_promo_woo'] = df_fase4.loc[mask_reorder, 'precio_venta'] * desc_reorder_est
    
    # Redondeo financiero estándar
    df_fase4['precios_promo_woo'] = df_fase4['precios_promo_woo'].round(2)

    # 3. Asignación Dinámica de Fechas Base
    df_fase4.loc[mask_seleccionados, 'fecha_inicio'] = fechas['fecha_inicio']
    df_fase4.loc[mask_seleccionados, 'fecha_fin'] = fechas['fecha_fin']
    
    # 4. Bloque Condicional Flash (Solo se ejecuta si el motor de fechas lo aprueba)
    if fechas['hay_flash']:
        # Corrección de llaves JSON para descuentos Flash
        desc_optimo_flash = 1 - (config_promo.get("optimo_flash_discount", 5) / 100)
        desc_sobrestock_flash  = 1 - (config_promo.get("sobrestock_flash_discount", 9) / 100)
        desc_reorder_flash  = 1 - (config_promo.get("reorder_flash_discount", 7) / 100)

        # Cálculos de Precios Flash
        df_fase4.loc[mask_reorder, 'precio_flash'] = df_fase4.loc[mask_reorder, 'precio_venta'] * desc_reorder_flash
        df_fase4.loc[mask_optimo, 'precio_flash'] = df_fase4.loc[mask_optimo, 'precio_venta'] * desc_optimo_flash
        df_fase4.loc[mask_sobrestock, 'precio_flash'] = df_fase4.loc[mask_sobrestock, 'precio_venta'] * desc_sobrestock_flash
        df_fase4['precio_flash'] = df_fase4['precio_flash'].round(2)

        # Inyección de fechas interrumpidas
        df_fase4.loc[mask_seleccionados, 'fecha_inicio_flash'] = fechas['fecha_inicio_flash']
        df_fase4.loc[mask_seleccionados, 'fecha_fin_flash'] = fechas['fecha_fin_flash']
        df_fase4.loc[mask_seleccionados, 'fecha_inicio_continuacion'] = fechas['fecha_inicio_continuacion']
        df_fase4.loc[mask_seleccionados, 'fecha_fin_continuacion'] = fechas['fecha_fin_continuacion']

    print("Precios estándar y Flash calculados correctamente.")
    print("Bloques de calendario inyectados en las filas seleccionadas.\n")
    
    return df_fase4


# In[ ]:


import os

def ejecutar_fase5_exportacion(df_entrada, ruta_destino):
    """
    Fase 5: Módulo de Exportación y Cierre.
    Limpia las columnas innecesarias (ruido) y genera el archivo físico CSV.
    """
    print("--- INICIANDO FASE 5: LIMPIEZA Y EXPORTACIÓN ---")
    
    if df_entrada is None or df_entrada.empty:
        print("ERROR: El DataFrame de entrada está vacío. No hay nada que exportar.")
        return None
        
    df_final = df_entrada.copy()
    
    # 1. Limpieza estricta de columnas innecesarias
    # Nota: Corregí la duplicidad de 'disponibilidad_millennium' de tu lista
    # e incluí 'disponibilidad_galerias' basándome en la estructura de tu matriz unificada.
    columnas_a_eliminar = [
        'inventario_entrante', 
        'velocidad', 
        'disponibilidad_millennium', 
        'disponibilidad_galerias', 
        'cantidad_en_transito'
    ]
    
    # errors='ignore' asegura que el código no falle si una columna ya no existe
    df_final = df_final.drop(columns=columnas_a_eliminar, errors='ignore')
    print("🧹 Columnas de ruido eliminadas con éxito.")
    
    # 2. Reordenamiento visual para facilidad de Marketing
    # Ponemos la información clave al principio y dejamos las fechas al final
    columnas_primero = [
        'sku', 'nombre', 'status_prediccion', 'seleccionado_promo', 
        'precio_venta', 'precios_promo_woo', 'precio_flash'
    ]
    # Traemos el resto de columnas que hayan sobrevivido a la limpieza
    columnas_resto = [col for col in df_final.columns if col not in columnas_primero]
    df_final = df_final[columnas_primero + columnas_resto]
    
    # 3. Exportación a CSV
    try:
        # Aseguramos que el directorio exista (seguridad del SO)
        os.makedirs(os.path.dirname(ruta_destino), exist_ok=True)
        
        # Guardado sin índice para que no se genere una columna extra de números
        df_final.to_csv(ruta_destino, index=False)
        print(f"✅ ¡ÉXITO! Archivo maestro promocional guardado en:\n📂 {ruta_destino}")
        
        # Auditoría de cierre
        total_exportado = len(df_final)
        total_promocionados = len(df_final[df_final['seleccionado_promo'] == 'x'])
        print(f"\n📊 RESUMEN DEL ENTREGABLE ORO:")
        print(f"   - Filas totales en el archivo: {total_exportado}")
        print(f"   - Ítems marcados con 'x' para promoción: {total_promocionados}")
        print("-" * 45)
        
        return df_final
        
    except Exception as e:
        print(f"ERROR al intentar guardar el archivo CSV: {e}")
        return None


# In[ ]:


# 1. Ingesta (Extrae el Dataframe y el Diccionario de Configuración)
ruta_archivo_maestro = "/home/night/Sistema_ERP_Intranet/output/df_inventario_omnicanal.csv"
ruta_configuracion = "/home/night/Sistema_ERP_Intranet/config/config.json"


# Definir la ruta establecida en tu arquitectura
#ruta_archivo_maestro = r"C:\Users\carlo\aProyecto_Automatizacion\ERP_intranet\Modulo_promociones\notebook_prueba\df_inventario_omnicanal.csv"
#ruta_configuracion = r"C:\Users\carlo\aProyecto_Automatizacion\ERP_intranet\Modulo_promociones\notebook_prueba\config.json"

# Ejecutar la Fase 1 y guardar el resultado en una variable para la siguiente fase
import datetime
df_candidatos, config_promo = ejecutar_fase1_ingesta(ruta_archivo_maestro, ruta_configuracion)

if df_candidatos is not None:
    fecha_simulada = datetime.datetime(2026, 10, 10, 12, 15, 56, 580688)
    # 2. Motor de Fechas (SE EJECUTA PRIMERO PARA SABER SI HAY FLASH)
    diccionario_fechas = ejecutar_fase3_motor_fechas(config_promo, fecha_simulada )
    
    # 3. Muestreo (Ahora recibe las fechas como parámetro)
    df_con_seleccion = ejecutar_fase2_muestreo(df_candidatos, config_promo, diccionario_fechas)
    
    # 4. Cálculos Financieros (Pasándole config_promo)
    df_matriz_calculada = ejecutar_fase4_calculos(df_con_seleccion, diccionario_fechas, config_promo)
    
    # 5. Limpieza y Exportación
    ruta_exportacion = "/home/night/Sistema_ERP_Intranet/output/df_inventario_promo.csv"
    #ruta_exportacion = r"C:\Users\carlo\aProyecto_Automatizacion\ERP_intranet\Modulo_promociones\notebook_prueba\output\df_inventario_promo.csv"
    df_oro = ejecutar_fase5_exportacion(df_matriz_calculada, ruta_exportacion)


# In[ ]:




