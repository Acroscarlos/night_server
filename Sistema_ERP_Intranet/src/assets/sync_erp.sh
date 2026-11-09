#!/bin/bash

# ==============================================================================
# SCRIPT DE SINCRONIZACIÓN ERP -> WORDPRESS (MATRIZ UNIFICADA)
# ==============================================================================
# Este script envía el archivo CSV maestro generado localmente hacia el endpoint
# seguro de la API REST en WordPress. Está diseñado para ejecutarse vía Cronjob.
# ==============================================================================

# ------------------------------------------------------------------------------
# 1. VARIABLES DE CONFIGURACIÓN (Llenar con los datos de producción)
# ------------------------------------------------------------------------------

# URL exacta del endpoint REST en tu WordPress
WP_ENDPOINT="https://mitiendaunit.com/wp-json/suite/v1/sync-csv"

# Token de seguridad (Debe coincidir con la constante SUITE_SYNC_SECRET de wp-config.php)
SYNC_TOKEN="RvTech_Sync_2026_xYz987!"

# Ruta absoluta al ÚNICO archivo CSV maestro
FILE_UNIFICADO="/home/night/Sistema_ERP_Intranet/output/Matriz_unificada_Woocommerce.csv"

# Ruta absoluta donde se guardará el registro de auditoría del script
LOG_FILE="/home/night/Sistema_ERP_Intranet/logs/sync.log"


# ------------------------------------------------------------------------------
# 2. FUNCIONES UTILITARIAS
# ------------------------------------------------------------------------------

# Función para registrar eventos en el log con fecha y hora exacta
log_message() {
    local MESSAGE=$1
    local TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")
    echo "[$TIMESTAMP] $MESSAGE" >> "$LOG_FILE"
}

# ------------------------------------------------------------------------------
# 3. VALIDACIÓN PREVIA (Evitar peticiones vacías al servidor)
# ------------------------------------------------------------------------------

# Comprobar existencia de la Matriz Unificada
if [ ! -f "$FILE_UNIFICADO" ]; then
    log_message "FALLIDO (Local) - El archivo $FILE_UNIFICADO no existe. Abortando."
    exit 1
fi


# ------------------------------------------------------------------------------
# 4. EJECUCIÓN DE cURL Y CAPTURA DE RESPUESTA
# ------------------------------------------------------------------------------

# Se ejecuta cURL en modo silencioso (-s), se oculta el cuerpo de respuesta (-o /dev/null) 
# y se extrae EXCLUSIVAMENTE el código de estado HTTP (-w "%{http_code}")
HTTP_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "$WP_ENDPOINT" \
    -H "X-Suite-Sync-Token: $SYNC_TOKEN" \
    -F "matriz_unificada=@$FILE_UNIFICADO")


# ------------------------------------------------------------------------------
# 5. ANÁLISIS DE LA RESPUESTA Y LOGGING
# ------------------------------------------------------------------------------

if [ "$HTTP_RESPONSE" -eq 200 ]; then
    log_message "EXITOSO (200) - Matriz Unificada enviada y procesada correctamente."
else
    # Manejo de errores comunes para brindar contexto en el log
    case $HTTP_RESPONSE in
        400) ERROR_MSG="Falta el archivo o el CSV es inválido (Bad Request)" ;;
        401) ERROR_MSG="Token de sincronización rechazado (Unauthorized)" ;;
        403) ERROR_MSG="Acceso denegado (Forbidden)" ;;
        404) ERROR_MSG="Endpoint no encontrado en WordPress" ;;
        500) ERROR_MSG="Error interno del servidor WordPress al mover el archivo" ;;
        *)   ERROR_MSG="Error HTTP desconocido" ;;
    esac

    log_message "FALLIDO ($HTTP_RESPONSE) - $ERROR_MSG."
    exit 1
fi

exit 0
