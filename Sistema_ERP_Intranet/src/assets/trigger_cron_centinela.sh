#!/bin/bash
# -------------------------------------------------------------------
# ERP V8 - Disparador Automatizado: Modo Centinela (Cierre de Caja)
# -------------------------------------------------------------------

LOG_FILE="/home/night/Sistema_ERP_Intranet/logs/cron_centinela.log"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] í ½í»¡ï¸ INICIANDO: EjecuciÃ³n de Cierre Centinela (Loyverse)..." >> "$LOG_FILE"

# PeticiÃ³n segura a la API REST del ERP
RESPONSE=$(curl -s -X POST -H "x-suite-sync-token: RvTech_Sync_2026_xYz987!" https://mitiendaunit.com/wp-json/suite/v1/trigger-cron/centinela-caja)

echo "[$(date '+%Y-%m-%d %H:%M:%S')] í ½í³¥ RESPUESTA: $RESPONSE" >> "$LOG_FILE"
echo "-------------------------------------------------------------------" >> "$LOG_FILE"