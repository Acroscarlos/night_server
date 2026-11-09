#!/bin/bash

# Definir la ruta absoluta del proyecto
PROJECT_DIR="/home/night/Sistema_Conciliacion"
cd $PROJECT_DIR

# Buscar si hay un proceso de python ejecutando main.py
if pgrep -f "python main.py" > /dev/null
then
    # El bot está vivo, no hacemos nada
    exit 0
else
    # El bot está caído (o el servidor se acaba de reiniciar). Lo levantamos.
    source venv/bin/activate
    
    # nohup lo ejecuta en segundo plano y guarda los logs en bot_forense.log
    nohup python main.py > bot_forense.log 2>&1 &
fi
