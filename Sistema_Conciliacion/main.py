import asyncio
import logging
import httpx
from aiogram import Bot, Dispatcher
from config import settings
from bot.middlewares import ZeroTrustMiddleware
from bot.handlers import router

# Activar logs en consola
logging.basicConfig(level=logging.INFO)

async def main():
    # 1. Inicializar Bot y Dispatcher
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()

    # 2. Registrar Middleware Zero-Trust y Router de comandos
    dp.message.middleware(ZeroTrustMiddleware())
    dp.callback_query.middleware(ZeroTrustMiddleware())
    dp.include_router(router)

    # 3. Inicializar Cliente HTTPX Global
    # Lo pasamos como kwargs en el dispatcher para que esté disponible en los handlers
    async with httpx.AsyncClient() as http_client:
        logging.info("🚀 Servidor iniciado. Escuchando a Telegram...")
        
        # Iniciamos el Long Polling (Ideal para pruebas locales)
        await dp.start_polling(bot, http_client=http_client)

if __name__ == "__main__":
    asyncio.run(main())