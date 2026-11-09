#!/usr/bin/env python
# coding: utf-8

import json
import logging
import requests
from pathlib import Path
from typing import Optional

logger = logging.getLogger("loyverse_api")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = BASE_DIR / "config" / "config.json"

class LoyverseAPI:
    """Cliente para la integración bidireccional con el inventario de Loyverse."""

    BASE_URL = "https://api.loyverse.com/v1.0"

    def __init__(self):
        self.token = None
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
                self.token = config.get("api", {}).get("loyverse_token")
        except Exception as e:
            logger.error(f"Error leyendo config.json: {e}")

        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def _get_variant_id_by_sku(self, sku: str) -> Optional[str]:
        """Busca el variant_id en Loyverse iterando por todas las páginas."""
        limit = 250
        cursor = None
        
        while True:
            params = {"limit": limit}
            if cursor:
                params["cursor"] = cursor
                
            response = self.session.get(f"{self.BASE_URL}/items", params=params)
            
            if response.status_code == 401:
                raise Exception("El Token de Loyverse es inválido o expiró. Revisa tu config.json.")
            response.raise_for_status()

            data = response.json()
            for item in data.get("items", []):
                for variant in item.get("variants", []):
                    # Validar coincidencia exacta eliminando espacios accidentales
                    if str(variant.get("sku")).strip() == str(sku).strip():
                        return variant.get("variant_id")
            
            cursor = data.get("cursor")
            if not cursor:
                break # Ya no hay más páginas
                
        return None

    def _get_current_stock(self, variant_id: str, store_id: str) -> float:
        """Obtiene el inventario actual de una variante en una tienda específica."""
        response = self.session.get(f"{self.BASE_URL}/inventory", params={"store_id": store_id})
        if response.status_code == 401:
            raise Exception("Token Inválido al consultar stock en Loyverse.")
        response.raise_for_status()

        for inv in response.json().get("inventory_levels", []):
            if inv.get("variant_id") == variant_id:
                return float(inv.get("in_stock", 0.0))
        return 0.0

    def adjust_inventory(self, sku: str, store_id: str, stock_change: float) -> bool:
        """Ajusta el stock sumando o restando la cantidad indicada."""
        if not self.token or len(str(self.token)) < 15:
            raise Exception(f"Token de Loyverse no configurado o muy corto. Actual: '{self.token}'")

        # 1. Obtener variant_id (Con paginación)
        variant_id = self._get_variant_id_by_sku(sku)
        if not variant_id:
            raise Exception(f"El SKU '{sku}' NO existe en Loyverse. Verifica que esté creado allá.")

        # 2. Calcular nuevo nivel
        current_stock = self._get_current_stock(variant_id, store_id)
        new_stock = current_stock + stock_change

        # 3. Enviar actualización
        payload = {"inventory_levels": [{"variant_id": variant_id, "store_id": store_id, "stock_after": new_stock}]}
        
        response = self.session.post(f"{self.BASE_URL}/inventory", json=payload)
        if response.status_code == 401:
            raise Exception("Token Inválido al intentar guardar el inventario.")
        response.raise_for_status()
        
        return True
    
    def get_recent_receipts(self, limit: int = 50) -> list:
        """Obtiene los recibos más recientes de Loyverse con sus líneas de detalle."""
        if not self.token:
            return []
        try:
            response = self.session.get(
                f"{self.BASE_URL}/receipts",
                params={"limit": limit}
            )
            response.raise_for_status()
            return response.json().get("receipts", [])
        except Exception as e:
            logger.error(f"Error obteniendo recibos de Loyverse: {str(e)}", exc_info=True)
            return []