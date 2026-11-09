#!/usr/bin/env python
# -*- coding: utf-8 -*-

import streamlit as st
import pandas as pd
import time
from sqlalchemy.exc import IntegrityError
from src.database.db_core import SessionLocal
from src.database.models import InventoryLocation
from src.logger_config import get_custom_logger

inv_logger = get_custom_logger("inventario", "modulo_inventario.log")

def render_catalogos_maestros_view():
    """Vista de administracion de Catalogos y Mapeos de API"""
    st.markdown("### Configuracion de Catalogos (Data Maestra)")
    st.info("Gestiona tus tiendas fisicas (vinculadas a Loyverse), bodegas y aliados comerciales.")

    # --- 1. TABLA INTERACTIVA DE UBICACIONES ---
    db = SessionLocal()
    try:
        locs = db.query(InventoryLocation).all()
        if locs:
            st.write("**Ubicaciones Actuales (Edita el ID de Loyverse directamente en la tabla y presiona Guardar):**")
            loc_data = [{"id": l.id, "Nombre": l.nombre, "Tipo": l.tipo, "Loyverse ID": l.loyverse_store_id or ""} for l in locs]
            df_locs = pd.DataFrame(loc_data)
            
            edited_locs = st.data_editor(
                df_locs, 
                hide_index=True, 
                width='stretch',
                disabled=["id", "Nombre", "Tipo"], 
                column_config={
                    "id": None,
                    "Loyverse ID": st.column_config.TextColumn("Loyverse Store ID (Dejar vacio si es Bodega o Aliado)")
                }
            )
            
            if st.button("Guardar Cambios en IDs de Loyverse", type="primary"):
                ids_validos = edited_locs['Loyverse ID'].replace("", None).dropna()
                if ids_validos.duplicated().any():
                    duplicados = ids_validos[ids_validos.duplicated()].unique()
                    st.error(f"[!] Error Critico: No puedes asignar el mismo Loyverse Store ID a multiples ubicaciones. Duplicados: {', '.join(duplicados)}")
                else:
                    for _, row in edited_locs.iterrows():
                        loc_to_update = db.query(InventoryLocation).filter_by(id=row['id']).first()
                        if loc_to_update:
                            nuevo_id = str(row['Loyverse ID']).strip() if pd.notna(row['Loyverse ID']) else ""
                            loc_to_update.loyverse_store_id = nuevo_id if nuevo_id else None
                    
                    try:
                        db.commit()
                        st.success("[+] IDs actualizados correctamente en la base de datos.")
                        time.sleep(1)
                        st.rerun()
                    except IntegrityError:
                        db.rollback()
                        st.error("[!] Error de integridad en la base de datos.")
    finally:
        db.close()

    st.markdown("---")

    # --- 2. FORMULARIO PARA CREAR NUEVAS UBICACIONES ---
    with st.expander("[+] Crear Nueva Ubicacion (Almacen, Tienda o Aliado)", expanded=False):
        with st.form("create_loc_form", clear_on_submit=True):
            l_nombre = st.text_input("Nombre de Ubicacion* (Ej. Tienda Galerias, Bodega Principal)")
            l_tipo = st.selectbox("Tipo de Ubicacion", ["Interno", "Aliado"])
            l_loy_id = st.text_input("Loyverse Store ID (Opcional - Pegalo solo si es una tienda oficial)")

            if st.form_submit_button("Guardar Nueva Ubicacion"):
                if l_nombre:
                    db = SessionLocal()
                    nueva_loc = InventoryLocation(
                        nombre=l_nombre, 
                        tipo=l_tipo,
                        loyverse_store_id=l_loy_id.strip() if l_loy_id.strip() else None
                    )
                    try:
                        db.add(nueva_loc)
                        db.commit()
                        inv_logger.info(f"Nueva ubicacion creada en catalogo: '{l_nombre}' (Tipo: {l_tipo})")
                        st.success(f"Ubicacion '{l_nombre}' creada con exito.")
                        time.sleep(1)
                        st.rerun()
                    except Exception as e:
                        db.rollback()
                        inv_logger.error(f"Error creando ubicacion '{l_nombre}': {e}", exc_info=True)
                        st.error(f"Error creando ubicacion: {e}")
                    finally:
                        db.close()
                else:
                    st.warning("El nombre es obligatorio.")