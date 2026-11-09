#!/bin/bash
# -------------------------------------------------------------------
# ERP V8 - Disparador Automatizado: Actualización de Tasa BCV Oficial
# -------------------------------------------------------------------

LOG_FILE="/home/night/Sistema_ERP_Intranet/logs/cron_materializar_gold.log"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] INICIANDO: Sincronización de Tabla Gold..." >> "$LOG_FILE"

# Petición segura a la API REST del ERP
RESPONSE=$(curl -s -X POST -H "x-suite-sync-token: RvTech_Sync_2026_xYz987!" https://mitiendaunit.com/wp-json/suite/v1/trigger-cron/materializar-gold)

echo "[$(date '+%Y-%m-%d %H:%M:%S')] RESPUESTA: $RESPONSE" >> "$LOG_FILE"
echo "-------------------------------------------------------------------" >> "$LOG_FILE"