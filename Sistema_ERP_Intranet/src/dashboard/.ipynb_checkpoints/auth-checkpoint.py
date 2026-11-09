#!/usr/bin/env python
# coding: utf-8

# Gestiona la seguridad básica mediante st.session_state y recarga la página al autenticar correctamente.
# 

# In[ ]:


import streamlit as st
import time
from src.dashboard.data_loader import load_config

def check_password() -> bool:
    """
    Retorna True si el usuario está logueado correctamente.
    Si no, muestra formulario de login y detiene la ejecución con st.stop().
    """
    # 1. Verificar si ya está autenticado en la sesión
    if st.session_state.get("authenticated", False):
        return True

    # 2. Cargar usuarios válidos desde config
    config = load_config()
    valid_users = config.get("dashboard", {}).get("usuarios_permitidos", {})

    # 3. Interfaz de Login
    st.markdown("## 🔐 Acceso al Dashboard Corporativo")
    
    with st.form("login_form"):
        username = st.text_input("Usuario")
        password = st.text_input("Contraseña", type="password")
        submit = st.form_submit_button("Iniciar Sesión")

        if submit:
            if username in valid_users and valid_users[username] == password:
                st.session_state["authenticated"] = True
                st.success("Acceso concedido. Cargando...")
                time.sleep(0.5)
                st.rerun()  # Recargar app para mostrar contenido
            else:
                st.error("Usuario o contraseña incorrectos.")

    # Detener la ejecución del resto de la app hasta que se loguee
    st.stop()
    return False

