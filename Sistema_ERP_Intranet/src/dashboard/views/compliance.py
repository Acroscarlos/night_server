#!/usr/bin/env python
# coding: utf-8

import streamlit as st
import pandas as pd
import json
import os
import shutil
import uuid
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from werkzeug.utils import secure_filename

# 1. Inyección de Auditoría Integral
from src.logger_config import get_custom_logger
legal_logger = get_custom_logger("legal", "modulo_legal.log")

# --- CONFIGURACIÓN DE RUTAS ---
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DATA_DIR = BASE_DIR / "data"

# Rutas Retrocompatibles (Viejas y Nuevas)
LEGACY_DOCS_DIR = DATA_DIR / "docs" / "permisos"
NEW_DOCS_DIR = DATA_DIR / "docs" / "legal"
META_FILE = DATA_DIR / "compliance_metadata.json"

# Asegurar estructura de directorios
NEW_DOCS_DIR.mkdir(parents=True, exist_ok=True)
LEGACY_DOCS_DIR.mkdir(parents=True, exist_ok=True)

# --- UTILIDADES ---
def _obtener_mes_espanol(mes: int) -> str:
    meses = {
        1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
        5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
        9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"
    }
    return meses.get(mes, "Desconocido")

# --- CAPA DE DATOS (MODELO) ---

def _load_metadata() -> pd.DataFrame:
    """ Carga la metadata y asegura que existan los campos nuevos (Migración Schema Segura). """
    if not META_FILE.exists():
        return pd.DataFrame(columns=[
            "id", "tienda", "ente", "categoria", "concepto",
            "emision", "vencimiento", "status_manual", "filenames"
        ])

    try:
        with open(META_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)

        df = pd.DataFrame(data)

        # Migración al vuelo (Blindada para no romper datos viejos)
        if "id" not in df.columns and not df.empty:
            df["id"] = [str(uuid.uuid4()) for _ in range(len(df))]
        if "concepto" not in df.columns and not df.empty:
            df["concepto"] = "Sin especificar"
        if "status_manual" not in df.columns and not df.empty:
            df["status_manual"] = "Vigente"
        if "categoria" not in df.columns and not df.empty:
            df["categoria"] = "Otros"
            
        # Migración crítica: Convertir 'filename' (string viejo) a 'filenames' (lista nueva)
        if "filenames" not in df.columns:
            if "filename" in df.columns:
                df["filenames"] = df["filename"].apply(lambda x: [f"LEGACY:{x}"] if pd.notnull(x) and x else [])
            else:
                df["filenames"] = [[] for _ in range(len(df))]

        return df
    except Exception as e:
        legal_logger.error(f"Error corrompido en metadata: {e}", exc_info=True)
        st.error(f"Error corrompido en metadata: {e}")
        return pd.DataFrame()

def _save_metadata(df: pd.DataFrame):
    """Persistencia atómica de metadata."""
    temp_file = META_FILE.with_suffix('.tmp')
    # Evitar guardar la columna vieja 'filename' si existe
    df_save = df.drop(columns=['filename'], errors='ignore')
    
    data = df_save.to_dict(orient="records")
    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    os.replace(temp_file, META_FILE)

def _save_documents(uploaded_files: list, doc_id_raw: str, concepto: str, ente: str, fecha_emision: str) -> tuple:
    """ 
    Guarda documentos en estructura Año/Mes/DOC-AAMMDD-HASH.
    Aplica nomenclatura estandarizada a los archivos.
    Retorna (lista_rutas_relativas, id_amigable).
    """
    if not uploaded_files:
        return [], ""

    now = datetime.now()
    anio_actual = str(now.year)
    mes_actual_es = _obtener_mes_espanol(now.month)

    # 1. Adaptación Point 1: Generar ID Amigable (DOC-AAMMDD-XXXX) basado en el UUID
    # Tomamos los primeros 4 caracteres del doc_id (UUID) original p/ mantener unicidad.
    uuid_hash = str(doc_id_raw).split('-')[0][:4].upper()
    fecha_corta = now.strftime('%y%m%d')
    id_amigable = f"DOC-{fecha_corta}-{uuid_hash}"

    # 2. Definir y crear directorio de destino con ID Amigable
    target_dir = NEW_DOCS_DIR / anio_actual / mes_actual_es / id_amigable
    target_dir.mkdir(parents=True, exist_ok=True)

    rutas_guardadas = []
    total_archivos = len(uploaded_files)

    for i, archivo in enumerate(uploaded_files, 1):
        # 3. Adaptación Point 1: Generar Nomenclatura Estandarizada de Archivo
        # Formato: Concepto_Ente_Fecha_(1_de_3).pdf
        
        # Sanitización estricta de variables para el sistema de archivos (quitar espacios y /)
        c_limpio = "".join([c for c in concepto if c.isalnum() or c in (' ', '-', '_')]).strip().replace(" ", "_")
        e_limpio = "".join([c for c in ente if c.isalnum() or c in (' ', '-', '_')]).strip().replace(" ", "_")
        
        # Mantener la extensión original (fallback a pdf si falla secure_filename)
        ext = secure_filename(archivo.name).split('.')[-1] if '.' in archivo.name else 'pdf'
        # Blindaje extra por seguridad
        if ext.lower() not in ['pdf', 'png', 'jpg', 'jpeg']: ext = 'pdf'

        nombre_estandarizado = f"{c_limpio}_{e_limpio}_{fecha_emision}_({i}_de_{total_archivos}).{ext}"

        file_path = target_dir / nombre_estandarizado
        try:
            with open(file_path, "wb") as f:
                f.write(archivo.getbuffer())
            
            # 4. Guardar la ruta relativa estructurada con el nuevo ID y Nombre
            ruta_relativa = f"{anio_actual}/{mes_actual_es}/{id_amigable}/{nombre_estandarizado}"
            rutas_guardadas.append(ruta_relativa)
            legal_logger.info(f"Archivo guardado: {ruta_relativa}")
        except Exception as e:
            legal_logger.error(f"Fallo al guardar archivo estandarizado {nombre_estandarizado}: {e}", exc_info=True)

    # Retornamos las rutas y el nuevo ID para que el padre actualice la BD SQL
    return rutas_guardadas, id_amigable

def _delete_document(doc_id: str, filenames: list):
    """Elimina el registro lógico y todos sus archivos físicos."""
    if not isinstance(filenames, list):
        filenames = [filenames] if pd.notnull(filenames) else []

    for fname in filenames:
        if not fname:
            continue
            
        # Determinar si es un archivo viejo o nuevo
        if str(fname).startswith("LEGACY:"):
            real_fname = str(fname).replace("LEGACY:", "")
            file_path = LEGACY_DOCS_DIR / real_fname
        else:
            file_path = NEW_DOCS_DIR / fname

        if file_path.exists():
            try:
                os.remove(file_path)
                legal_logger.info(f"Archivo físico eliminado: {file_path.name}")
            except OSError as e:
                legal_logger.error(f"No se pudo borrar archivo físico: {e}", exc_info=True)
                st.warning(f"No se pudo borrar el archivo físico: {e}")

    # Intentar limpiar la subcarpeta del ID (Solo para archivos nuevos)
    if filenames and not str(filenames[0]).startswith("LEGACY:"):
        try:
            dir_path = (NEW_DOCS_DIR / filenames[0]).parent
            if dir_path.exists() and not os.listdir(dir_path):
                os.rmdir(dir_path)
        except Exception as e:
            pass # Ignoramos errores de borrado de carpeta si aún tiene archivos

    # Eliminar Lógico
    df = _load_metadata()
    df = df[df['id'] != doc_id]
    _save_metadata(df)
    legal_logger.info(f"Registro lógico {doc_id} eliminado exitosamente.")

# --- LÓGICA DE NEGOCIO (LEGALTECH) ---

def _calculate_traffic_light(row) -> str:
    if row.get('status_manual') == 'Archivado':
        return '⚫ Archivado'

    try:
        # Extraemos fecha soportando formato texto o datetime
        fecha_str = str(row['vencimiento']).split(' ')[0]
        venc = datetime.strptime(fecha_str, "%Y-%m-%d").date()
    except:
        return '⚪ Error Fecha'

    days_left = (venc - date.today()).days

    if row.get('status_manual') == 'En Renovación':
        return '🔵 En Gestión (Renovación)'

    if days_left < 0:
        return '🔴 VENCIDO'
    if days_left <= 60:
        return f'🟡 Vence en {days_left} días'

    return '🟢 Vigente'

# --- INTERFAZ DE USUARIO (VISTA) ---

def render_compliance_view():
    st.markdown("### ⚖️ Repositorio Legal & Cumplimiento")

    df = _load_metadata()

    # --- SECCIÓN SUPERIOR: DASHBOARD DE ALERTAS ---
    if not df.empty:
        df['Estado Calculado'] = df.apply(_calculate_traffic_light, axis=1)
        # Contamos cuántos archivos tiene cada registro
        df['Archivos Adjuntos'] = df['filenames'].apply(lambda x: len(x) if isinstance(x, list) else 0)

        # Filtros de KPIs (Uso de shape[0] para evitar errores de Pandas)
        total_docs = len(df)
        vencidos = df[df['Estado Calculado'] == '🔴 VENCIDO'].shape[0] 
        renovacion = df[df['Estado Calculado'] == '🔵 En Gestión (Renovación)'].shape[0]
        por_vencer = df[df['Estado Calculado'].str.contains('🟡', na=False)].shape[0]

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Documentos Totales", total_docs)
        k2.metric("🚨 Acción Inmediata", vencidos, delta="Riesgo Legal" if vencidos > 0 else "OK", delta_color="inverse")
        k3.metric("🔵 En Trámite", renovacion, help="Vencidos pero con gestión iniciada")
        k4.metric("📅 Alerta 60 días", por_vencer, help="Planificación preventiva")

        if vencidos > 0 or por_vencer > 0:
            st.warning(f"⚠️ ATENCIÓN: Hay **{vencidos} documentos vencidos** sin gestión y **{por_vencer} por vencer** próximamente.")
    else:
        st.info("Sistema de Compliance inicializado. No hay documentos registrados.")

    st.divider()

    # --- TABS PRINCIPALES (SISTEMA DE PÍLDORAS) ---
    opciones_legal = ["📂 Repositorio & Gestión", "➕ Nuevo Documento"]
    vista_seleccionada = st.pills("Sub-Navegación Legal", options=opciones_legal, default=opciones_legal[0], label_visibility="collapsed")

    # ---------------------------------------------------------
    # TAB 1: REPOSITORIO INTERACTIVO (TU PATRÓN MASTER-DETAIL INTACTO)
    # ---------------------------------------------------------
    if vista_seleccionada == "📂 Repositorio & Gestión":
        if df.empty:
            st.caption("Utilice la pestaña 'Nuevo Documento' para cargar información.")
        else:
            col_f1, col_f2 = st.columns(2)
            filtro_tienda = col_f1.multiselect("Filtrar por Tienda", df['tienda'].unique())
            filtro_ente = col_f2.multiselect("Filtrar por Ente", df['ente'].unique())

            df_view = df.copy()
            if filtro_tienda:
                df_view = df_view[df_view['tienda'].isin(filtro_tienda)]
            if filtro_ente:
                df_view = df_view[df_view['ente'].isin(filtro_ente)]

            st.markdown("##### 📑 Listado Maestro de Permisos")

            # Mantenemos el interactive dataframe
            event = st.dataframe(
                df_view[[
                    'Estado Calculado', 'tienda', 'ente', 'categoria', 'concepto',
                    'vencimiento', 'status_manual', 'Archivos Adjuntos'
                ]],
                width='stretch',
                hide_index=True,
                column_config={
                    "Estado Calculado": st.column_config.TextColumn("Estado (Auto)", width="medium"),
                    "status_manual": st.column_config.TextColumn("Workflow Actual", width="small"),
                    "vencimiento": st.column_config.DateColumn("Vence", format="DD/MM/YYYY"),
                    "categoria": st.column_config.TextColumn("Categoría"),
                    "Archivos Adjuntos": st.column_config.NumberColumn("📁 Adjuntos", format="%d")
                },
                selection_mode="single-row",
                on_select="rerun"
            )

            # Panel de Acciones (Master-Detail Pattern Restaurado)
            if len(event.selection.rows) > 0:
                selected_idx = event.selection.rows[0]
                record = df_view.iloc[selected_idx]

                st.info(f"Gestionando: **{record['ente']} - {record['concepto']}**")
                c_act1, c_act2, c_act3 = st.columns(3)

                # Acción 1: Multi-Descargas Inteligentes
                with c_act1:
                    st.write("⬇️ Descargar Documentos")
                    lista_archivos = record.get('filenames', [])
                    if not lista_archivos:
                        st.warning("⚠️ Sin adjuntos físicos")
                    else:
                        for idx, fname in enumerate(lista_archivos):
                            # Resolver ruta (Legacy vs New)
                            if str(fname).startswith("LEGACY:"):
                                real_fname = str(fname).replace("LEGACY:", "")
                                file_path = LEGACY_DOCS_DIR / real_fname
                                btn_label = "📄 Descargar Permiso (Legado)"
                            else:
                                file_path = NEW_DOCS_DIR / fname
                                btn_label = f"📄 Descargar Archivo {idx+1}"

                            if file_path.exists():
                                with open(file_path, "rb") as pdf_file:
                                    st.download_button(
                                        label=btn_label,
                                        data=pdf_file,
                                        file_name=file_path.name,
                                        mime='application/pdf',
                                        key=f"dl_{record['id']}_{idx}"
                                    )
                            else:
                                st.error(f"❌ Archivo no encontrado: {file_path.name}")

                # Acción 2: Actualizar Estatus
                with c_act2:
                    opciones = ["Vigente", "En Renovación", "Archivado"]
                    idx_status = opciones.index(record['status_manual']) if record['status_manual'] in opciones else 0
                    new_status = st.selectbox("Actualizar Estatus:", opciones, index=idx_status, key=f"st_{record['id']}")
                    
                    if new_status != record['status_manual']:
                        df.loc[df['id'] == record['id'], 'status_manual'] = new_status
                        _save_metadata(df)
                        legal_logger.info(f"Estatus actualizado a {new_status} para el ID {record['id']}")
                        st.success("Estatus actualizado")
                        st.rerun()

                # Acción 3: Borrar
                with c_act3:
                    st.write("🗑️ Zona de Peligro")
                    if st.button("Eliminar Registro", type="primary", key=f"del_{record['id']}"):
                        _delete_document(record['id'], record.get('filenames', []))
                        st.success("Documento y archivos eliminados.")
                        st.rerun()
            else:
                st.caption("👈 Seleccione un documento en la tabla para ver las opciones de descarga y edición.")

    # ---------------------------------------------------------
    # TAB 2: NUEVO DOCUMENTO (FORMULARIO RESTAURADO)
    # ---------------------------------------------------------
    elif vista_seleccionada == "➕ Nuevo Documento":
        st.markdown("#### 📂 Carga de Nuevo Documento Legal")

        with st.form("upload_compliance_form", clear_on_submit=True):
            col_a, col_b = st.columns(2)

            with col_a:
                # Restauradas tus listas desplegables originales
                tienda = st.selectbox("Sede / Tienda", ["F67- GA (Galerías)", "C3-MM (Millennium)", "Corporativo"])
                ente = st.selectbox("Ente Regulador", ["Seniat", "Alcaldía", "Bomberos", "IVSS", "Banavih", "Min. Trabajo", "Sanidad", "Otro"])
                categoria = st.selectbox("Categoría Legal *", ["Permisos Municipales", "Contratos", "Fiscales", "Laborales", "Patentes", "Otros"])

            with col_b:
                concepto = st.text_input("Concepto / Descripción", placeholder="Ej: Patente Industria y Comercio 2024")
                col_d1, col_d2 = st.columns(2)
                fecha_emision = col_d1.date_input("Fecha Emisión", value=date.today())
                fecha_venc = col_d2.date_input("Fecha Vencimiento", value=date.today() + timedelta(days=365))
                status_inicial = st.selectbox("Estatus Inicial", ["Vigente", "En Renovación", "Archivado"])

            # 🚀 El nuevo Multi-Uploader con límite visual
            archivos = st.file_uploader("Adjuntar Documentos Físicos (Límite: 5 archivos permitidos)", accept_multiple_files=True, type=["pdf", "png", "jpg", "jpeg"])

            submitted = st.form_submit_button("💾 Registrar en Repositorio")

            if submitted:
                if not archivos or not concepto:
                    st.error("❌ Error: Debe subir al menos un archivo y especificar el Concepto.")
                elif len(archivos) > 5:
                    st.error("❌ Violación de Políticas: No puedes subir más de 5 archivos a la vez para un mismo registro.")
                else:
                    try:
                        new_id = str(uuid.uuid4())
                        
                        # 🚨 CORRECCIÓN: Le pasamos los nuevos argumentos para nombrar los archivos
                        rutas_guardadas, nuevo_id = _save_documents(
                            uploaded_files=archivos, 
                            doc_id_raw=new_id, 
                            concepto=concepto, 
                            ente=ente, 
                            fecha_emision=str(fecha_emision)
                        )

                        new_record = {
                            "id": nuevo_id, # Usamos el ID amigable (DOC-AAMMDD-XXXX)
                            "tienda": tienda,
                            "ente": ente,
                            "categoria": categoria,
                            "concepto": concepto,
                            "emision": str(fecha_emision),
                            "vencimiento": str(fecha_venc),
                            "status_manual": status_inicial,
                            "filenames": rutas_guardadas # Guardamos la lista de rutas relativas
                        }

                        df_new = pd.DataFrame([new_record])
                        df = pd.concat([df, df_new], ignore_index=True)
                        _save_metadata(df)

                        # 🚨 CORRECCIÓN: El logger y el success ahora muestran el nuevo_id
                        legal_logger.info(f"Registro legal creado: ID {nuevo_id} | Adjuntos: {len(rutas_guardadas)}")
                        st.success(f"✅ Documento registrado exitosamente ({nuevo_id}) con {len(rutas_guardadas)} archivo(s) adjunto(s).")
                        time.sleep(1.5)
                        st.rerun()

                    except Exception as e:
                        legal_logger.error("Fallo general durante la creación del nuevo registro legal.", exc_info=True)
                        st.error(f"Ocurrió un error en la base de datos: {e}")