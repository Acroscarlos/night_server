#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Módulo Generador de Planillas E-Commerce (MercadoLibre / Cashea)
Opera 100% en memoria RAM (BytesIO) leyendo el maestro omnicanal en tiempo real.
"""

import streamlit as st
import pandas as pd
import openpyxl
import re
import io
import os
from pathlib import Path
from datetime import datetime
from src.logger_config import get_custom_logger

# Logger dedicado para auditar qué vendedor genera qué planilla
planillas_logger = get_custom_logger("planillas_ecommerce", "planillas_ecommerce.log")

# Ruta estricta en el servidor Ubuntu
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
RUTA_CSV_MAESTRO = BASE_DIR / "output" / "df_inventario_omnicanal.csv"


def _preparar_maestro_csv(ruta_csv: Path) -> dict:
    """
    FASE 2: Ingesta y Preparación del Archivo Maestro desde Ubuntu.
    Transforma el CSV en un diccionario indexado por SKU (Búsqueda O(1)).
    """
    if not ruta_csv.exists():
        st.error(f"🚨 No se encontró el archivo maestro en el servidor: `{ruta_csv}`")
        return None

    try:
        df_maestro = pd.read_csv(ruta_csv, encoding='utf-8-sig')

        if 'sku' not in df_maestro.columns:
            st.error("❌ El archivo CSV omnicanal no contiene la columna obligatoria `'sku'`.")
            return None

        # Limpieza estructural del SKU
        df_maestro['sku'] = df_maestro['sku'].astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
        df_maestro = df_maestro[df_maestro['sku'] != 'nan']

        # 🛡️ BLINDAJE DE STOCK FÍSICO: Evitar sumar mercancía en tránsito si la columna existe
        if 'cantidad_en_transito' in df_maestro.columns and 'stock_total' in df_maestro.columns:
            transito = pd.to_numeric(df_maestro['cantidad_en_transito'], errors='coerce').fillna(0)
            stock_tot = pd.to_numeric(df_maestro['stock_total'], errors='coerce').fillna(0)
            # Solo descontamos si stock_total venía inflado con tránsito; aseguramos piso 0
            df_maestro['stock_fisico_seguro'] = stock_tot.clip(lower=0)
        else:
            df_maestro['stock_fisico_seguro'] = pd.to_numeric(df_maestro.get('stock_total', 0), errors='coerce').fillna(0).clip(lower=0)

        # Eliminamos duplicados de SKU quedándonos con la primera aparición para evitar ValueError en set_index
        df_maestro = df_maestro.drop_duplicates(subset=['sku'], keep='first')
        df_maestro.set_index('sku', inplace=True)

        return df_maestro.to_dict(orient='index')

    except Exception as e:
        planillas_logger.error(f"Fallo crítico al procesar CSV maestro: {e}", exc_info=True)
        st.error(f"❌ Error leyendo la base de datos omnicanal: {e}")
        return None


def _obtener_indices_columnas(hoja, fila_cabecera: int, lista_nombres_esperados: list) -> dict:
    """Mapea dinámicamente el nombre de cada cabecera con su índice numérico de columna (1-indexed)."""
    mapa_columnas = {}
    for celda in hoja[fila_cabecera]:
        valor_celda = str(celda.value).strip() if celda.value else ""
        if valor_celda in lista_nombres_esperados:
            mapa_columnas[valor_celda] = celda.column
    return mapa_columnas


def _procesar_plantilla_en_memoria(archivo_subido, plataforma: str, inventario_maestro: dict, descuento_pct: float, precio_tope: float, stock_maximo: int = None):
    """
    FASES 3 a 6: Carga el Excel en RAM, inyecta reglas de negocio y retorna el archivo binario listo para descarga.
    """
    try:
        # Cargamos el archivo directamente desde el buffer de memoria de Streamlit
        bytes_entrada = io.BytesIO(archivo_subido.getvalue())
        wb = openpyxl.load_workbook(bytes_entrada)

        if plataforma == "MERCADOLIBRE":
            nombres_hojas = ["Publicaciones"]
            fila_cabecera = 3
            fila_inicio_datos = 5
            nom_col_stock = "Stock en tu depósito"
            nom_col_precio = "Precio"
            nom_col_estado = "Estado"
            nom_col_pub = "Número de publicación"
            columnas_objetivo = ["SKU", nom_col_stock, nom_col_precio, nom_col_estado, nom_col_pub]

        elif plataforma == "CASHEA":
            nombres_hojas = [h for h in wb.sheetnames if "instrucciones" not in h.lower()]
            fila_cabecera = 3
            fila_inicio_datos = 6
            nom_col_stock = "Stock"
            nom_col_precio = "Precio (USD)"
            nom_col_estado = "Estado"
            columnas_objetivo = ["Descripción", nom_col_stock, nom_col_precio, nom_col_estado]
        else:
            return None, None

        total_procesados = 0
        actualizaciones_exitosas = 0
        hojas_afectadas = []

        for nombre_hoja in nombres_hojas:
            if nombre_hoja not in wb.sheetnames:
                continue

            hoja = wb[nombre_hoja]
            mapa_cols = _obtener_indices_columnas(hoja, fila_cabecera, columnas_objetivo)

            if len(mapa_cols) < len(columnas_objetivo):
                continue

            hojas_afectadas.append(nombre_hoja)

            # =======================================================
            # FASE 4A: PRE-PROCESAMIENTO POR PUBLICACIÓN (SOLO ML)
            # =======================================================
            memoria_precios_ml = {}
            if plataforma == "MERCADOLIBRE":
                for f in range(fila_inicio_datos, hoja.max_row + 1):
                    val_pub = hoja.cell(row=f, column=mapa_cols[nom_col_pub]).value
                    val_sku = hoja.cell(row=f, column=mapa_cols["SKU"]).value

                    if val_pub and val_sku:
                        pub_str = str(val_pub).strip()
                        sku_str = str(val_sku).strip().replace(".0", "")

                        if sku_str in inventario_maestro and pub_str not in memoria_precios_ml:
                            precio_base = float(inventario_maestro[sku_str].get('precio_venta', 0.0))
                            precio_rebajado = precio_base * (1.0 - (descuento_pct / 100.0))

                            if precio_rebajado > precio_tope:
                                memoria_precios_ml[pub_str] = {"precio": round(precio_rebajado, 2), "estado_forzado": "Pausada"}
                            else:
                                memoria_precios_ml[pub_str] = {"precio": round(precio_rebajado, 2), "estado_forzado": None}

            # =======================================================
            # FASES 4B Y 5: CRUCE E INYECCIÓN FINAL (PASE 2)
            # =======================================================
            for fila in range(fila_inicio_datos, hoja.max_row + 1):
                sku_extraido = None
                pub_actual = None

                if plataforma == "MERCADOLIBRE":
                    val_pub = hoja.cell(row=fila, column=mapa_cols[nom_col_pub]).value
                    if val_pub:
                        pub_actual = str(val_pub).strip()

                    valor_celda = hoja.cell(row=fila, column=mapa_cols["SKU"]).value
                    if valor_celda:
                        sku_extraido = str(valor_celda).strip().replace(".0", "")

                elif plataforma == "CASHEA":
                    valor_desc = str(hoja.cell(row=fila, column=mapa_cols["Descripción"]).value)
                    if valor_desc and valor_desc.lower() != "none":
                        coincidencia = re.search(r"(?i)SKU\s*:\s*([a-zA-Z0-9-]+)", valor_desc)
                        if coincidencia:
                            sku_extraido = coincidencia.group(1).strip()

                # --- LÓGICA MERCADOLIBRE ---
                if plataforma == "MERCADOLIBRE" and pub_actual in memoria_precios_ml:
                    datos_pub = memoria_precios_ml[pub_actual]
                    hoja.cell(row=fila, column=mapa_cols[nom_col_precio]).value = datos_pub["precio"]

                    if datos_pub["estado_forzado"]:
                        hoja.cell(row=fila, column=mapa_cols[nom_col_estado]).value = datos_pub["estado_forzado"]

                    if sku_extraido and sku_extraido in inventario_maestro:
                        total_procesados += 1
                        datos_erp = inventario_maestro[sku_extraido]
                        velocidad = float(datos_erp.get('velocidad', 0.0))
                        stock_base = int(float(datos_erp.get('stock_fisico_seguro', datos_erp.get('stock_total', 0))))

                        nuevo_stock = max(0, stock_base - 1) if velocidad > 0.2 else max(0, stock_base)
                        if stock_maximo is not None:
                            nuevo_stock = min(nuevo_stock, stock_maximo)
                        hoja.cell(row=fila, column=mapa_cols[nom_col_stock]).value = nuevo_stock

                        if not datos_pub["estado_forzado"]:
                            nuevo_estado = "Pausada" if nuevo_stock == 0 else "Activa"
                            hoja.cell(row=fila, column=mapa_cols[nom_col_estado]).value = nuevo_estado

                        actualizaciones_exitosas += 1

                # --- LÓGICA CASHEA ---
                elif plataforma == "CASHEA" and sku_extraido and sku_extraido in inventario_maestro:
                    total_procesados += 1
                    datos_erp = inventario_maestro[sku_extraido]

                    velocidad = float(datos_erp.get('velocidad', 0.0))
                    stock_base = int(float(datos_erp.get('stock_fisico_seguro', datos_erp.get('stock_total', 0))))
                    precio_base = float(datos_erp.get('precio_venta', 0.0))

                    nuevo_stock = max(0, stock_base - 1) if velocidad > 0.2 else max(0, stock_base)
                    if stock_maximo is not None:
                        nuevo_stock = min(nuevo_stock, stock_maximo)
                    precio_rebajado = precio_base * (1.0 - (descuento_pct / 100.0))

                    if precio_rebajado > precio_tope:
                        nuevo_precio = 80000.0
                        nuevo_estado = "Pausada"
                    else:
                        nuevo_precio = round(precio_rebajado, 2)
                        nuevo_estado = "Pausada" if nuevo_stock == 0 else "Activa"

                    hoja.cell(row=fila, column=mapa_cols[nom_col_stock]).value = nuevo_stock
                    hoja.cell(row=fila, column=mapa_cols[nom_col_precio]).value = nuevo_precio
                    hoja.cell(row=fila, column=mapa_cols[nom_col_estado]).value = nuevo_estado
                    actualizaciones_exitosas += 1

        # Si ninguna hoja coincidió con la estructura de la plataforma seleccionada
        if not hojas_afectadas:
            return None, {"error": f"El archivo subido no tiene el formato ni las columnas esperadas para {plataforma}."}

        # Guardamos el libro modificado en un buffer de memoria RAM
        output_buffer = io.BytesIO()
        wb.save(output_buffer)
        output_buffer.seek(0)

        metricas = {
            "skus_maestro": len(inventario_maestro),
            "hojas_procesadas": len(hojas_afectadas),
            "items_cruzados": total_procesados,
            "items_actualizados": actualizaciones_exitosas
        }
        return output_buffer, metricas

    except Exception as e:
        planillas_logger.error(f"Error procesando Excel en memoria ({plataforma}): {e}", exc_info=True)
        return None, {"error": str(e)}


# ==============================================================================
# FUNCIÓN PRINCIPAL DE RENDERIZADO (UI STREAMLIT)
# ==============================================================================
def app_planillas_ecommerce():
    """Interfaz aislada para generación de planillas de MercadoLibre y Cashea."""
    usuario_actual = st.session_state.get("username", "Vendedor")

    st.markdown("### 🛍️ Generador Automático de Planillas E-Commerce")
    st.caption("Sincroniza tus plantillas de **MercadoLibre** y **Cashea** con el inventario omnicanal en vivo.")

    # Verificación del estado y fecha de actualización del archivo maestro en el servidor
    if RUTA_CSV_MAESTRO.exists():
        mtime = datetime.fromtimestamp(os.path.getmtime(RUTA_CSV_MAESTRO)).strftime('%d/%m/%Y %I:%M:%S %p')
        st.info(f"📡 **Fuente de Datos Conectada:** `df_inventario_omnicanal.csv` | **Última actualización en servidor:** `{mtime}`")
    else:
        st.error(f"🚨 Atención: El archivo `{RUTA_CSV_MAESTRO}` no se encuentra disponible en este momento.")
        return

    with st.container(border=True):
        col_plat, col_desc, col_tope, col_stock = st.columns(4)

        with col_plat:
            plataforma = st.selectbox(
                "1️⃣ Selecciona la Plataforma",
                options=["CASHEA", "MERCADOLIBRE"],
                help="Elige el formato exacto de la plantilla que vas a subir."
            )

        with col_desc:
            porcentaje_descuento = st.number_input(
                "2️⃣ Descuento Comercial (%)",
                min_value=0.0,
                max_value=50.0,
                value=0.0,
                step=0.5,
                format="%.1f",
                help="Porcentaje de descuento a aplicar sobre el precio de venta base."
            )

        with col_tope:
            precio_tope = st.number_input(
                "3️⃣ Precio Tope Límite ($ USD)",
                min_value=10.0,
                max_value=50000.0,
                value=3000.0,
                step=50.0,
                format="%.2f",
                help="Artículos que superen este monto serán pausados automáticamente por protección."
            )
            
        with col_stock:
            stock_input = st.number_input(
                "4️⃣ Stock Máximo",
                min_value=1,
                value=5,
                step=1,
                help="Tope de unidades por artículo. Borra el número (dejar vacío) para asignar el stock total.",
                placeholder="Stock total"
            )
            # En Streamlit, si el usuario borra la celda, retorna None
            stock_maximo = int(stock_input) if stock_input is not None else None

        st.divider()

        archivo_excel = st.file_uploader(
            f"5️⃣ Sube aquí tu plantilla Excel original de {plataforma} (.xlsx)",
            type=["xlsx"],
            help="El sistema conservará intactas las hojas ocultas, metadatos e instrucciones."
        )

        procesar_btn = st.button("⚙️ Procesar y Sincronizar Plantilla", type="primary", use_container_width=True, disabled=(archivo_excel is None))

    if procesar_btn and archivo_excel is not None:
        with st.spinner(f"Cruzando inventario en vivo e inyectando reglas para {plataforma}..."):
            inventario_maestro = _preparar_maestro_csv(RUTA_CSV_MAESTRO)

            if inventario_maestro:
                buffer_salida, resultado = _procesar_plantilla_en_memoria(
                    archivo_subido=archivo_excel,
                    plataforma=plataforma,
                    inventario_maestro=inventario_maestro,
                    descuento_pct=porcentaje_descuento,
                    precio_tope=precio_tope,
                    stock_maximo=stock_maximo
                )

                if buffer_salida is None:
                    st.error(f"❌ No se pudo procesar la plantilla: {resultado.get('error', 'Error desconocido')}")
                else:
                    # Construimos el nombre de salida respetando tu estándar _ERP.xlsx
                    nombre_orig = archivo_excel.name
                    nombre_base, ext = os.path.splitext(nombre_orig)
                    nombre_descarga = f"{nombre_base}_ERP{ext}"

                    planillas_logger.info(
                        f"Usuario '{usuario_actual}' generó planilla {plataforma} "
                        f"(Desc: {porcentaje_descuento}%, Tope: ${precio_tope}). "
                        f"Actualizados: {resultado['items_actualizados']} items."
                    )

                    st.success("✅ ¡Plantilla generada exitosamente en memoria!")

                    k1, k2, k3 = st.columns(3)
                    k1.metric("📦 SKUs en Servidor", resultado["skus_maestro"])
                    k2.metric("📄 Hojas Procesadas", resultado["hojas_procesadas"])
                    k3.metric("🎯 Items Actualizados", resultado["items_actualizados"])

                    st.download_button(
                        label=f"⬇️ Descargar {nombre_descarga}",
                        data=buffer_salida,
                        file_name=nombre_descarga,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        type="primary",
                        use_container_width=True
                    )