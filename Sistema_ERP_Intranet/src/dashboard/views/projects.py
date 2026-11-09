#!/usr/bin/env python
# coding: utf-8

"""
Módulo de Proyectos (Refactorizado V7.2 - Arquitectura RDBMS)
Integra la vista Kanban de Streamlit con una base de datos relacional (SQLAlchemy)
para evitar colisiones multiusuario, usando sincronización atómica.
"""

import streamlit as st
import json
import os
import shutil
import uuid
import time
from werkzeug.utils import secure_filename
from pathlib import Path
from datetime import datetime, date
from typing import List, Dict, Any

# --- IMPORTACIONES DE BASE DE DATOS ---
from src.database.db_core import SessionLocal
from src.database.models import Project, ProjectChecklist, ProjectComment

# --- CONFIGURACIÓN DE RUTAS ---
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DATA_DIR = BASE_DIR / "data"
FILES_DIR = DATA_DIR / "projects_files"

# Asegurar directorios
DATA_DIR.mkdir(parents=True, exist_ok=True)
FILES_DIR.mkdir(parents=True, exist_ok=True)

# ==========================================
# CAPA DE DATOS: GESTIÓN DE PROYECTOS (ORM)
# ==========================================

def _load_projects() -> List[Dict[str, Any]]:
    """
    Carga los proyectos desde la BD y reconstruye la estructura 
    de diccionario anidado EXACTA que espera la vista Kanban.
    """
    db = SessionLocal()
    projects_list = []
    try:
        db_projects = db.query(Project).all()
        
        for p in db_projects:
            # TRADUCCIÓN BBDD -> UI: Mapeamos los campos de la BD a la estructura del Frontend
            project_dict = {
                "id": p.id,
                "titulo": p.titulo,
                "description": p.descripcion, # UI espera 'description'
                "owner": p.owner,
                "shared_with": p.shared_with if p.shared_with else [],
                "status": p.status,
                "prioridad": p.prioridad,
                "fechas": {"inicio": p.fecha_inicio, "fin": p.fecha_fin}, # UI espera dict anidado
                "archivado": p.archivado,
                "archivos": p.archivos if p.archivos else [],
                
                "checklist": [{"texto": cl.texto, "done": cl.done} for cl in p.checklists],
                "comentarios": [
                    # UI espera 'user', 'text' y 'date'
                    {"user": com.usuario, "text": com.texto, "date": com.fecha_hora} 
                    for com in p.comentarios
                ]
            }
            projects_list.append(project_dict)
            
        return projects_list
    except Exception as e:
        st.error(f"Error cargando proyectos desde DB: {e}")
        return []
    finally:
        db.close()


def _save_projects(data: List[Dict[str, Any]]):
    """
    Persistencia atómica del Kanban usando SQLAlchemy.
    Sincroniza el estado visual actual con las tablas relacionales.
    """
    db = SessionLocal()
    try:
        for p_data in data:
            db_proj = db.query(Project).filter_by(id=p_data["id"]).first()
            
            if not db_proj:
                db_proj = Project(id=p_data["id"])
                db.add(db_proj)
            
            # TRADUCCIÓN UI -> BBDD
            db_proj.titulo = p_data.get("titulo", "")
            db_proj.descripcion = p_data.get("description", "")
            db_proj.owner = p_data.get("owner", "")
            db_proj.shared_with = p_data.get("shared_with", [])
            db_proj.status = p_data.get("status", "To Do")
            db_proj.prioridad = p_data.get("prioridad", "Media")
            
            # Aplanar el diccionario de fechas
            fechas = p_data.get("fechas", {})
            db_proj.fecha_inicio = fechas.get("inicio", "")
            db_proj.fecha_fin = fechas.get("fin", "")
            
            db_proj.archivado = p_data.get("archivado", False)
            db_proj.archivos = p_data.get("archivos", [])

            # --- Sincronización de Relaciones Hijas ---
            db.query(ProjectChecklist).filter_by(project_id=db_proj.id).delete()
            for cl in p_data.get("checklist", []):
                nuevo_cl = ProjectChecklist(
                    project_id=db_proj.id, 
                    texto=cl.get("texto", ""), 
                    done=cl.get("done", False)
                )
                db.add(nuevo_cl)

            db.query(ProjectComment).filter_by(project_id=db_proj.id).delete()
            for com in p_data.get("comentarios", []):
                nuevo_com = ProjectComment(
                    project_id=db_proj.id, 
                    usuario=com.get("user", "Sistema"), 
                    texto=com.get("text", ""), 
                    fecha_hora=com.get("date", "")
                )
                db.add(nuevo_com)

        db.commit()
    except Exception as e:
        db.rollback() 
        st.error(f"Error crítico guardando proyectos en DB: {e}")
    finally:
        db.close()

def _save_attachment(uploaded_file, project_id) -> str:
    """Guarda un archivo físico sanitizado y retorna el nombre final del archivo."""
    if uploaded_file is None: return None
        
    project_folder = FILES_DIR / str(project_id)
    project_folder.mkdir(parents=True, exist_ok=True)
    
    filename = secure_filename(uploaded_file.name)
    if not filename:
        filename = f"adjunto_{int(time.time())}.pdf"
        
    file_path = project_folder / filename
    with open(file_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
        
    return filename # Retornamos solo el nombre para guardarlo en la BD

# ==========================================
# UTILIDADES VISUALES
# ==========================================

def _get_priority_color(priority):
    colors = {
        "Alta": "🔴",    # Rojo
        "Media": "🟡",   # Amarillo
        "Baja": "🔵"     # Azul
    }
    return colors.get(priority, "⚪")

def _calculate_progress(checklist):
    if not checklist:
        return 0.0, "0/0"
    total = len(checklist)
    done = sum(1 for item in checklist if item['done'])
    return done / total, f"{done}/{total}"

# ==========================================
# VISTA PRINCIPAL (KANBAN)
# ==========================================

def render_projects_view():
    current_user = st.session_state.get("username", "invitado")

    # Header y Filtro de Archivo
    col_head_1, col_head_2 = st.columns([3, 1])
    with col_head_1:
        st.markdown(f"### 🚀 Gestión de Proyectos")
    with col_head_2:
        show_archived = st.checkbox("📂 Ver Archivados", value=False)

    # --- SECCIÓN 1: CREACIÓN DE PROYECTO ---
    with st.expander("✨ Nuevo Proyecto", expanded=False):
        with st.form("new_project_form"):
            c1, c2 = st.columns(2)
            titulo = c1.text_input("Título del Proyecto")
            prioridad = c2.selectbox("Prioridad", ["Alta", "Media", "Baja"], index=1)
            
            c3, c4 = st.columns(2)
            inicio = c3.date_input("Fecha Inicio", value=date.today())
            fin = c4.date_input("Fecha Fin", value=date.today())
            
            desc = st.text_area("Descripción Inicial")
            shared_input = st.text_input("Compartir con (separar por comas)", placeholder="admin, gerente...")

            if st.form_submit_button("Crear Tarjeta"):
                projects = _load_projects()
                shared_list = [s.strip() for s in shared_input.split(",") if s.strip()]
                
                new_proj = {
                    "id": str(uuid.uuid4())[:8],
                    "titulo": titulo,
                    "description": desc,
                    "owner": current_user,
                    "shared_with": shared_list,
                    "status": "To Do",
                    "created_at": str(date.today()),
                    "fechas": {"inicio": str(inicio), "fin": str(fin)},
                    "prioridad": prioridad,
                    "checklist": [],
                    "comentarios": [],
                    "archivos": [],
                    "archivado": False
                }
                projects.append(new_proj)
                _save_projects(projects)
                st.success("Proyecto creado exitosamente en BD.")
                st.rerun()

    # --- SECCIÓN 2: LÓGICA DE FILTRADO Y CARGA ---
    all_projects = _load_projects()
    visible_projects = []

    # Row Level Security
    for p in all_projects:
        is_owner = p.get('owner') == current_user
        is_admin = current_user == "admin"
        is_shared = current_user in p.get('shared_with', [])
        
        # Filtro de visibilidad + Filtro de Archivados
        if (is_owner or is_admin or is_shared):
            if p.get('archivado', False) == show_archived:
                visible_projects.append(p)

    if not visible_projects:
        if show_archived:
            st.info("No hay proyectos archivados.")
        else:
            st.info("No hay tarjetas visibles. ¡Crea una nueva!")
        return

    # --- SECCIÓN 3: KANBAN BOARD ---
    col_todo, col_prog, col_done = st.columns(3)
    cols_map = {"To Do": col_todo, "In Progress": col_prog, "Done": col_done}

    col_todo.markdown("#### 📝 Por Hacer")
    col_prog.markdown("#### 🚧 En Progreso")
    col_done.markdown("#### ✅ Terminado")

    # Renderizado de Tarjetas
    for proj in visible_projects:
        status = proj.get('status', 'To Do')
        
        if status not in cols_map: status = "To Do"

        with cols_map[status]:
            with st.container(border=True):
                # 1. Cabecera de Tarjeta
                p_icon = _get_priority_color(proj.get('prioridad', 'Media'))
                prog_val, prog_text = _calculate_progress(proj.get('checklist', []))
                
                st.markdown(f"**{p_icon} {proj['titulo']}**")
                
                if proj.get('checklist'):
                    st.progress(prog_val)
                    st.caption(f"Tareas: {prog_text}")
                
                st.caption(f"📅 Fin: {proj.get('fechas', {}).get('fin', 'Sin fecha')}")
                
                # 2. Detalles (Expander)
                with st.expander("📖 Abrir Detalles"):
                    st.markdown(f"_{proj.get('description', '')}_")
                    st.markdown(f"**Dueño:** {proj.get('owner')}")
                    st.divider()

                    # A. Checklist Interactiva
                    st.markdown("**Checklist**")
                    new_item = st.text_input("Agregar tarea", key=f"new_item_{proj['id']}")
                    if st.button("➕", key=f"add_item_{proj['id']}") and new_item:
                        proj['checklist'].append({'texto': new_item, 'done': False})
                        _save_projects(all_projects)
                        st.rerun()

                    for idx, item in enumerate(proj.get('checklist', [])):
                        chk_key = f"chk_{proj['id']}_{idx}"
                        is_done = st.checkbox(item['texto'], value=item['done'], key=chk_key)
                        if is_done != item['done']:
                            item['done'] = is_done
                            _save_projects(all_projects)
                            st.rerun()
                    
                    st.divider()

                    # B. Comentarios (Chat)
                    st.markdown("**Comentarios**")
                    for comm in proj.get('comentarios', []):
                        st.caption(f"👤 **{comm['user']}** ({comm['date']}):")
                        st.text(comm['text'])
                    
                    new_comm = st.text_input("Escribir comentario...", key=f"comm_{proj['id']}")
                    if st.button("Enviar", key=f"send_{proj['id']}") and new_comm:
                        proj['comentarios'].append({
                            "user": current_user,
                            "text": new_comm,
                            "date": datetime.now().strftime("%Y-%m-%d %H:%M")
                        })
                        _save_projects(all_projects)
                        st.rerun()

                    st.divider()

                    # C. Archivos Adjuntos
                    st.markdown("**Adjuntos**")
                    uploaded = st.file_uploader("Subir archivo", key=f"up_{proj['id']}")
                    if uploaded:
                        if st.button("Guardar Archivo", key=f"save_file_{proj['id']}"):
                            # 1. Guardamos y obtenemos el nombre sanitizado
                            safe_filename = _save_attachment(uploaded, proj['id'])
                            
                            if safe_filename:
                                # 2. Guardamos EL NOMBRE SEGURO en la base de datos
                                proj['archivos'].append(safe_filename)
                                _save_projects(all_projects)
                                st.success("Archivo guardado")
                                st.rerun()
                    
                    if proj.get('archivos'):
                        for fname in proj['archivos']:
                            full_path = FILES_DIR / proj['id'] / fname
                            if full_path.exists():
                                with open(full_path, "rb") as f:
                                    st.download_button(f"⬇️ {fname}", f, file_name=fname, key=f"dl_{proj['id']}_{fname}")

                # 3. Controles de Movimiento
                btn_col1, btn_col2 = st.columns(2)
                
                # Botón Izquierda
                with btn_col1:
                    if status == "In Progress":
                        if st.button("⬅️", key=f"back_to_todo_{proj['id']}"):
                            proj['status'] = "To Do"
                            _save_projects(all_projects)
                            st.rerun()
                    elif status == "Done":
                        if st.button("⬅️", key=f"back_to_prog_{proj['id']}"):
                            proj['status'] = "In Progress"
                            proj['archivado'] = False 
                            _save_projects(all_projects)
                            st.rerun()

                # Botón Derecha / Archivar
                with btn_col2:
                    if status == "To Do":
                        if st.button("➡️", key=f"adv_to_prog_{proj['id']}"):
                            proj['status'] = "In Progress"
                            _save_projects(all_projects)
                            st.rerun()
                    elif status == "In Progress":
                        if st.button("➡️", key=f"adv_to_done_{proj['id']}"):
                            proj['status'] = "Done"
                            _save_projects(all_projects)
                            st.rerun()
                    elif status == "Done":
                        if proj.get('archivado'):
                            if st.button("♻️", help="Restaurar", key=f"rest_{proj['id']}"):
                                proj['archivado'] = False
                                _save_projects(all_projects)
                                st.rerun()
                        else:
                            if st.button("🗄️", help="Archivar", key=f"arch_{proj['id']}"):
                                proj['archivado'] = True
                                _save_projects(all_projects)
                                st.rerun()