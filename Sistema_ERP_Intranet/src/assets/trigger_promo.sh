#!/bin/bash

# ==============================================================================
# SCRIPT DE SINCRONIZACIÓN ERP -> WORDPRESS (MARKETING / PROMOS)
# ==============================================================================

# URL del nuevo endpoint REST dedicado a Marketing
WP_ENDPOINT="https://mitiendaunit.com/wp-json/suite/v1/sync-promo"

# Token de seguridad (Igual al de la Matriz Unificada)
SYNC_TOKEN="RvTech_Sync_2026_xYz987!"

# Ruta absoluta al archivo de promociones
FILE_PROMO="/home/night/Sistema_ERP_Intranet/output/df_inventario_promo.csv"

# Log de auditoría
LOG_FILE="/home/night/Sistema_ERP_Intranet/logs/cron_promo_inventory.log"

log_message() {
    local MESSAGE=$1
    local TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")
    echo "[$TIMESTAMP] $MESSAGE" >> "$LOG_FILE"
}

if [ ! -f "$FILE_PROMO" ]; then
    log_message "FALLIDO (Local) - El archivo $FILE_PROMO no existe. Abortando."
    exit 1
fi

# Envío vía HTTP POST (Multipart)
HTTP_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "$WP_ENDPOINT" \
    -H "X-Suite-Sync-Token: $SYNC_TOKEN" \
    -F "promo_file=@$FILE_PROMO")

if [ "$HTTP_RESPONSE" -eq 200 ]; then
    log_message "EXITOSO (200) - Matriz de Promociones enviada y procesada correctamente."
else
    case $HTTP_RESPONSE in
        400) ERROR_MSG="Falta el archivo o el CSV es inválido (Bad Request)" ;;
        401) ERROR_MSG="Token de sincronización rechazado (Unauthorized)" ;;
        403) ERROR_MSG="Acceso denegado (Forbidden)" ;;
        404) ERROR_MSG="Endpoint no encontrado en WordPress" ;;
        500) ERROR_MSG="Error interno del servidor WordPress" ;;
        *)   ERROR_MSG="Error HTTP desconocido" ;;
    esac
    log_message "FALLIDO ($HTTP_RESPONSE) - $ERROR_MSG."
    exit 1
fi
exit 0