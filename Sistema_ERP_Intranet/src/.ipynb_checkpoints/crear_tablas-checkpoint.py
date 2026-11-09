#!/usr/bin/env python
# coding: utf-8

"""
Herramienta DevSecOps para generar credenciales hasheadas del ERP.
Uso: python hash_generator.py
"""

import getpass
from werkzeug.security import generate_password_hash

def main():
    print("="*50)
    print("🔐 GENERADOR DE CREDENCIALES SEGURAS - ERP V7")
    print("="*50)
    
    username = input("Ingresa el nombre del nuevo usuario: ").strip()
    
    # getpass oculta la contraseña en la consola mientras se escribe
    password = getpass.getpass(f"Ingresa la contraseña para '{username}': ")
    password_confirm = getpass.getpass("Confirma la contraseña: ")
    
    if password != password_confirm:
        print("❌ Las contraseñas no coinciden. Intenta de nuevo.")
        return

    # Genera el hash (Werkzeug usa pbkdf2:sha256 con salt por defecto)
    secure_hash = generate_password_hash(password)
    
    print("\n✅ ¡HASH GENERADO CON ÉXITO!")
    print("\nAbre tu archivo config/config.json y reemplaza o agrega lo siguiente dentro de 'usuarios_permitidos':")
    print("-" * 50)
    print(f'"{username}": "{secure_hash}"')
    print("-" * 50)

if __name__ == "__main__":
    main()