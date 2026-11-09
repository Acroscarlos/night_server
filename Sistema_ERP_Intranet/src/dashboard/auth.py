
#!/usr/bin/env python
# coding: utf-8

"""
Módulo de Autenticación DevSecOps (Refactorizado V7)
Implementa JWT (JSON Web Tokens), Cookies persistentes y validación de Hashes.
"""

import streamlit as st
import time
import jwt
import secrets
import os
import tempfile
import json
from datetime import datetime, timedelta, timezone
from werkzeug.security import check_password_hash, generate_password_hash
from streamlit_cookies_controller import CookieController



# Secreto para firmar el JWT seguro (Generación dinámica si no existe en producción)
JWT_SECRET = os.getenv("ERP_JWT_SECRET")
if not JWT_SECRET:
    JWT_SECRET = secrets.token_hex(32)

def check_password() -> bool:
    """
    Gestiona la autenticación de usuarios.
    Retorna True si la sesión es válida, False en caso contrario.
    """
    # ==========================================
    # 🛡️ AISLAMIENTO DE SESIÓN Y COMPONENTE
    # Instanciamos el controlador SIEMPRE en el flujo principal (NO en session_state).
    # Esto garantiza que cada computadora tenga su instancia y evitamos contaminación cruzada.
    # ==========================================
    cookie_controller = CookieController(key="erp_cookies")

    # ==========================================
    # 1. INTERCEPTOR DEVSECOPS: Cierre de Sesión Seguro
    # ==========================================
    if st.session_state.get("force_logout", False):
        cookie_controller.remove("erp_session_token")
        
        # Limpiamos todas las banderas de memoria
        claves_seguridad = ["authenticated", "username", "force_logout", "f5_sync_done"]
        for k in claves_seguridad:
            if k in st.session_state:
                del st.session_state[k]
                
        st.success("✅ Sesión cerrada correctamente.")
        time.sleep(1) # Pausa breve para que el navegador procese el borrado web
        if hasattr(st, "rerun"): st.rerun()
        else: st.experimental_rerun()

    # ==========================================
    # 2. VERIFICACIÓN RÁPIDA (Memoria RAM)
    # ==========================================
    if st.session_state.get("authenticated", False):
        return True

    # ==========================================
    # 3. VERIFICACIÓN PERSISTENTE (Lectura de Cookies)
    # ==========================================
    token = cookie_controller.get("erp_session_token")
    
    # ⏳ EL TRUCO DEL RENDIMIENTO (F5 SYNC)
    # En el milisegundo 1 después de presionar F5, el navegador aún no ha tenido 
    # tiempo de enviarle las cookies a Python. El token será None.
    # En lugar de pedir login inmediatamente, detenemos el script (st.stop) 
    # para forzar al frontend a renderizar y enviarnos la cookie.
    if token is None:
        if "f5_sync_done" not in st.session_state:
            st.session_state["f5_sync_done"] = True
            st.markdown("### 🔒 Acceso Seguro al Sistema ERP")
            with st.spinner("Sincronizando credenciales seguras..."):
                pass
            # Detenemos la ejecución. El frontend renderizará, leerá la cookie 
            # y disparará un RERUN automático e invisible hacia Python.
            st.stop()
            
    # Si llegamos aquí y tenemos token, lo validamos
    if token:
        try:
            decoded_token = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            st.session_state["authenticated"] = True
            st.session_state["username"] = decoded_token["user"]
            return True
            
        except jwt.ExpiredSignatureError:
            st.warning("⏱️ Tu sesión ha expirado (Límite 2 horas). Por favor, ingresa de nuevo.")
            cookie_controller.remove("erp_session_token")
        except jwt.InvalidTokenError:
            st.error("⚠️ Detectamos un problema de seguridad. Inicia sesión nuevamente.")
            cookie_controller.remove("erp_session_token")

    # ==========================================
    # 4. INTERFAZ DE LOGIN (Si definitivamente no hay token)
    # ==========================================
    st.markdown("### 🔒 Acceso Seguro al Sistema ERP")
    
    with st.form("login_form"):
        username = st.text_input("Usuario")
        password = st.text_input("Contraseña", type="password")
        submit = st.form_submit_button("Iniciar Sesión")

        if submit:
            from src.dashboard.data_loader import load_config
            config = load_config()
            usuarios_permitidos = config.get("dashboard", {}).get("usuarios_permitidos", {})

            if username in usuarios_permitidos:
                stored_hash = usuarios_permitidos[username]
                
                if check_password_hash(stored_hash, password):
                    st.session_state["authenticated"] = True
                    st.session_state["username"] = username
                    
                    # Generar JWT 2 horas
                    expiration_time = datetime.now(timezone.utc) + timedelta(hours=2)
                    new_token = jwt.encode(
                        {"user": username, "exp": expiration_time},
                        JWT_SECRET,
                        algorithm="HS256"
                    )
                    
                    cookie_controller.set("erp_session_token", new_token, max_age=7200)
                    
                    st.success("✅ Autenticación exitosa. Cargando entorno...")
                    time.sleep(1) 
                    
                    if hasattr(st, "rerun"): st.rerun()
                    else: st.experimental_rerun()
                else:
                    st.error("❌ Contraseña incorrecta.")
            else:
                st.error("❌ Usuario no registrado.")
                
    st.stop()
    return False

def admin_reset_password(admin_username: str, admin_password: str, target_user: str, new_password: str, config_path: str) -> tuple[bool, str]:
    """
    Motor de reseteo seguro. Exige validación cruzada del administrador antes de escribir.
    """
    try:
        # 1. Leer el archivo de configuración actual
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
            
        usuarios = config.get("dashboard", {}).get("usuarios_permitidos", {})
        
        # 2. VALIDACIÓN DE LLAVE MAESTRA (Seguridad estricta)
        if admin_username not in usuarios:
            return False, "Credenciales de Administrador no válidas."
            
        if not check_password_hash(usuarios[admin_username], admin_password):
            return False, "Llave Maestra (Contraseña de Administrador) incorrecta."
            
        # 3. Validar existencia del usuario destino
        if target_user not in usuarios:
            return False, f"El usuario '{target_user}' no existe en la base de datos."
            
        # 4. Generar el nuevo hash criptográfico (Automáticamente usará scrypt)
        new_hash = generate_password_hash(new_password)
        
        # 5. Actualizar el diccionario en memoria
        config["dashboard"]["usuarios_permitidos"][target_user] = new_hash
        
        # 6. ESCRITURA ATÓMICA (Protección contra apagones)
        # Escribimos en un archivo temporal y luego reemplazamos el original de golpe
        temp_fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(config_path), suffix=".json")
        with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
            
        os.replace(temp_path, config_path)
        
        return True, f"Contraseña de '{target_user}' actualizada exitosamente."
        
    except Exception as e:
        return False, f"Error crítico en el motor criptográfico: {str(e)}"



