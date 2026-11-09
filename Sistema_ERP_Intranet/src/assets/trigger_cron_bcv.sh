#!/bin/bash
# -------------------------------------------------------------------
# ERP V8 - Disparador Automatizado: ActualizaciÃ³n de Tasa BCV Oficial
# -------------------------------------------------------------------

LOG_FILE="/home/night/Sistema_ERP_Intranet/logs/cron_bcv.log"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] í ½í´„ INICIANDO: SincronizaciÃ³n de Tasa BCV Oficial..." >> "$LOG_FILE"

# PeticiÃ³n segura a la API REST del ERP
RESPONSE=$(curl -s -X POST -H "x-suite-sync-token: RvTech_Sync_2026_xYz987!" https://mitiendaunit.com/wp-json/suite/v1/trigger-cron/actualizar-bcv)

echo "[$(date '+%Y-%m-%d %H:%M:%S')] í ½í³¥ RESPUESTA: $RESPONSE" >> "$LOG_FILE"
echo "-------------------------------------------------------------------" >> "$LOG_FILE"