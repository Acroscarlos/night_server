#!/bin/bash

# ==============================================================================
# SCRIPT DE SINCRONIZACIÓN Y ACTUALIZACIÓN: AUDITORÍA DE LEDGER
# ==============================================================================

# 1. Configuración de Credenciales y Rutas
API_URL="https://mitiendaunit.com/wp-json/suite/v1/export-ledger"
TOKEN="RvTech_Sync_2026_xYz987!"

# 2. Rutas del Servidor Local
OUTPUT_DIR="/home/night/Sistema_ERP_Intranet/output"
FILE_NAME="Top_Vendedores_Woo.csv"
FULL_PATH="$OUTPUT_DIR/$FILE_NAME"

# 3. Lógica de Preparación
# Garantizamos que la carpeta de destino exista antes de descargar
mkdir -p "$OUTPUT_DIR"

echo "[$(date +'%Y-%m-%d %H:%M:%S')] Iniciando solicitud de actualización del Ledger..."

# 4. Ejecución de la Petición y Sobrescritura (Actualización)
# El parámetro -o sobrescribe el archivo existente con la data fresca.
# El parámetro -w extrae el código de estado HTTP para validarlo.
CACHE_BUSTER=$(date +%s)

HTTP_STATUS=$(curl -X GET "$API_URL?t=$CACHE_BUSTER" \
     -H "x-suite-sync-token: $TOKEN" \
     -H "Cache-Control: no-cache" \
     -H "Pragma: no-cache" \
     -w "%{http_code}" \
     -o "$FULL_PATH" \
     -s)

# 5. Validación de la Lógica de Actualización
if [ "$HTTP_STATUS" -eq 200 ]; then
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] ✅ Sincronización exitosa. El archivo $FILE_NAME ha sido actualizado."
else
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] ❌ Error en la sincronización. Código HTTP: $HTTP_STATUS"
    # Opcional: Podrías enviar una alerta a Telegram aquí si el cron falla
fi
