import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    SOCIOS_WHITELIST: str
    BDV_API_KEY: str

    class Config:
        env_file = ".env"

settings = Settings()

# Convertimos la lista de strings separada por comas en un Set de enteros para búsqueda ultra rápida
WHITELIST_IDS = {int(uid.strip()) for uid in settings.SOCIOS_WHITELIST.split(",") if uid.strip()}