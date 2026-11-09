import asyncio
import httpx
import re
from datetime import datetime, timedelta
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import settings

router = Router()

# ==========================================
# 🧠 MÁQUINA DE ESTADOS (FSM)
# ==========================================
class ConsultaMovimientos(StatesGroup):
    esperando_fechas = State()

# ==========================================
# 🧹 HIGIENE: AUTODESTRUCCIÓN ASÍNCRONA
# ==========================================
async def auto_destruct_task(bot: Bot, chat_id: int, message_id: int, delay_seconds: int = 300):
    await asyncio.sleep(delay_seconds)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass # Si el usuario borró el chat manualmente antes de los 5 min, ignoramos el error

# ==========================================
# 🎮 MENÚ PRINCIPAL
# ==========================================
@router.message(F.text == "/start")
async def send_menu(message: Message, state: FSMContext):
    await state.clear() # Limpiamos cualquier estado colgado
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Consultar Saldo", callback_data="menu_saldo")],
        [InlineKeyboardButton(text="🔍 Últimos Movimientos", callback_data="menu_movimientos")]
    ])
    await message.answer("👨‍💻 *Panel Financiero UNI-T*\n\nSeleccione una operación:", reply_markup=keyboard, parse_mode="Markdown")

@router.callback_query(F.data == "back_home")
async def back_home(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Consultar Saldo", callback_data="menu_saldo")],
        [InlineKeyboardButton(text="🔍 Últimos Movimientos", callback_data="menu_movimientos")]
    ])
    await callback.message.edit_text("👨‍💻 *Panel Financiero UNI-T*\n\nSeleccione una operación:", reply_markup=keyboard, parse_mode="Markdown")

# ==========================================
# 💰 FLUJO DE SALDO
# ==========================================
@router.callback_query(F.data == "menu_saldo")
async def submenu_saldo(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏦 Cuenta ...7965", callback_data="saldo_01020384830000307965")],
        [InlineKeyboardButton(text="🏦 Cuenta ...3484", callback_data="saldo_01020497690001573484")],
        [InlineKeyboardButton(text="🔙 Volver al Menú", callback_data="back_home")]
    ])
    await callback.message.edit_text("Seleccione la cuenta a consultar el saldo:", reply_markup=keyboard)

@router.callback_query(F.data.startswith("saldo_"))
async def fetch_saldo(callback: CallbackQuery, bot: Bot, http_client: httpx.AsyncClient):
    cuenta = callback.data.split("_")[1]
    await callback.answer("⏳ Consultando BDV...")
    
    payload = {"currency": "VES", "account": cuenta}
    headers = {"X-API-KEY": settings.BDV_API_KEY, "Content-Type": "application/json"}
    
    try:
        response = await http_client.post("https://bdvconciliacion.banvenez.com/account/balance", json=payload, headers=headers, timeout=15.0)
        body = response.json()
        
        # 🛡️ FIX: Forzamos la validación a String (Tolerancia Antifrágil)
        if response.status_code == 200 and str(body.get("code", "")) == "1000":
            saldo = body.get("ppalSdoFinal", "0.00")
            msg_text = f"✅ *Saldo Disponible*\n\n🏦 Cuenta: `{cuenta}`\n💵 Saldo: `{saldo} VES`\n\n_⏱️ Se autodestruirá en 5 min._"
        else:
            msg_text = f"❌ *Error del Banco*\n`{body.get('message', 'Respuesta desconocida')}`\n\n_JSON Crudo: {body}_\n\n_⏱️ Se autodestruirá en 5 min._"
            
    except Exception as e:
        msg_text = f"⚠️ *Fallo de Conexión*\n{e}\n_⏱️ Se autodestruirá en 5 min._"

    msg_enviado = await callback.message.answer(msg_text, parse_mode="Markdown")
    asyncio.create_task(auto_destruct_task(bot, callback.message.chat.id, msg_enviado.message_id, 300))

# ==========================================
# 🔍 FLUJO DE MOVIMIENTOS
# ==========================================
@router.callback_query(F.data == "menu_movimientos")
async def submenu_movimientos(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏦 Cuenta ...7965", callback_data="movs_01020384830000307965")],
        [InlineKeyboardButton(text="🏦 Cuenta ...3484", callback_data="movs_01020497690001573484")],
        [InlineKeyboardButton(text="🔙 Volver al Menú", callback_data="back_home")]
    ])
    await callback.message.edit_text("Seleccione la cuenta para ver los últimos movimientos (3 días):", reply_markup=keyboard)

# 1. Petición Rápida (Últimos 3 días por defecto)
@router.callback_query(F.data.startswith("movs_"))
async def fetch_movimientos_rapido(callback: CallbackQuery, bot: Bot, http_client: httpx.AsyncClient):
    cuenta = callback.data.split("_")[1]
    
    fecha_fin = datetime.now()
    fecha_ini = fecha_fin - timedelta(days=3)
    str_ini = fecha_ini.strftime("%d/%m/%Y")
    str_fin = fecha_fin.strftime("%d/%m/%Y")
    
    await callback.message.edit_text("⏳ *Conectando con BDV...*\n_Extrayendo movimientos recientes..._", parse_mode="Markdown")
    
    texto_resultado = await consultar_y_formatear_movimientos(cuenta, str_ini, str_fin, http_client)
    
    # Botones para buscar otra fecha
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Buscar otra fecha", callback_data=f"askdate_{cuenta}")],
        [InlineKeyboardButton(text="🔙 Volver al Menú", callback_data="back_home")]
    ])
    
    msg_enviado = await callback.message.answer(texto_resultado, parse_mode="Markdown", reply_markup=keyboard)
    asyncio.create_task(auto_destruct_task(bot, callback.message.chat.id, msg_enviado.message_id, 300))

# 2. Transición FSM: Solicitar Rango de Fechas
@router.callback_query(F.data.startswith("askdate_"))
async def pedir_fechas(callback: CallbackQuery, state: FSMContext):
    cuenta = callback.data.split("_")[1]
    
    # Guardamos la cuenta en la memoria temporal del estado
    await state.update_data(cuenta_seleccionada=cuenta)
    await state.set_state(ConsultaMovimientos.esperando_fechas)
    
    await callback.message.answer("📅 *Búsqueda Específica*\n\nIngrese el rango de fechas que desea consultar en formato `DD/MM/YYYY - DD/MM/YYYY`.\n\n_Ejemplo: 01/06/2026 - 05/06/2026_", parse_mode="Markdown")
    await callback.answer()

# 3. Handler FSM: Recibir y Procesar Fechas
@router.message(ConsultaMovimientos.esperando_fechas)
async def procesar_fechas_custom(message: Message, state: FSMContext, bot: Bot, http_client: httpx.AsyncClient):
    texto = message.text.strip()
    
    # 🛡️ Regex Extractor Antifrágil: Sacará las fechas sin importar si puso espacios o no
    fechas_encontradas = re.findall(r"\d{2}/\d{2}/\d{4}", texto)
    
    if len(fechas_encontradas) != 2:
        await message.answer("❌ *Formato inválido.*\nNo pude detectar las dos fechas. Por favor, escriba el inicio y fin (Ej: 01/06/2026 - 05/06/2026).", parse_mode="Markdown")
        return # Sale sin borrar el estado para que pueda intentarlo de nuevo
        
    fecha_ini, fecha_fin = fechas_encontradas[0], fechas_encontradas[1]
    
    # Recuperar la cuenta guardada
    data = await state.get_data()
    cuenta = data.get("cuenta_seleccionada")
    
    msg_espera = await message.answer("⏳ *Consultando base de datos del BDV...*", parse_mode="Markdown")
    
    texto_resultado = await consultar_y_formatear_movimientos(cuenta, fecha_ini, fecha_fin, http_client)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Volver al Menú", callback_data="back_home")]
    ])
    
    msg_final = await msg_espera.edit_text(texto_resultado, parse_mode="Markdown", reply_markup=keyboard)
    asyncio.create_task(auto_destruct_task(bot, message.chat.id, msg_final.message_id, 300))
    
    # ¡CRÍTICO! Limpiar el estado para que el bot regrese a la normalidad
    await state.clear()

# ==========================================
# ⚙️ FUNCIÓN CORE: CONSUMO API BDV Y FORMATO
# ==========================================
async def consultar_y_formatear_movimientos(cuenta: str, f_ini: str, f_fin: str, http_client: httpx.AsyncClient) -> str:
    payload = {
        "cuenta": cuenta,
        "fechaIni": f_ini,
        "fechaFin": f_fin,
        "tipoMoneda": "VES",
        "nroMovimiento": ""
    }
    headers = {"X-API-KEY": settings.BDV_API_KEY, "Content-Type": "application/json"}
    
    try:
        resp = await http_client.post("https://bdvconciliacion.banvenez.com/apis/bdv/consulta/movimientos", json=payload, headers=headers, timeout=20.0)
        body = resp.json()
        
        # Validación relajada como string
        if resp.status_code == 200 and str(body.get("code", "")) == "1000":
            data_bdv = body.get("data", {})
            movs = data_bdv.get("movs", [])
            
            if not movs:
                return f"📊 *Extracto del {f_ini} al {f_fin}*\n\n_No se encontraron movimientos en este rango de fechas._\n\n_⏱️ Se autodestruirá en 5 min._"
            
            lineas = [f"📊 *Extracto BDV: {f_ini} al {f_fin}*\n"]
            # Iterar los últimos 15 para evitar sobrepasar el límite de caracteres de Telegram
            for m in movs[:15]: 
                tipo = str(m.get("mov", "")).upper()
                is_credito = (tipo == "CREDITO")
                signo = "+" if is_credito else "-"
                icono = "🟢" if is_credito else "🔴"
                
                # Limpieza matemática estricta ("3.000,91" -> "3000.91")
                importe_str = str(m.get("importe", "0")).replace(".", "").replace(",", ".")
                try:
                    valor = float(importe_str)
                except:
                    valor = 0.0
                
                ref = m.get("referencia", "N/A")
                fecha = m.get("fecha", "")
                concepto = str(m.get("descripcion", m.get("observacion", "Sin detalle")))
                concepto = concepto[:35] + "..." if len(concepto) > 35 else concepto
                
                linea = f"{icono} *{signo}{valor:,.2f} VES*\n├ 📄 Ref: `{ref}`\n├ 🗓 {fecha}\n└ 💬 _{concepto}_\n"
                lineas.append(linea)
            
            lineas.append("\n_⏱️ Se autodestruirá en 5 min._")
            return "\n".join(lineas)
        else:
            return f"❌ *Error del Banco*\n`{body.get('message', 'Desconocido')}`\n\n_⏱️ Se autodestruirá en 5 min._"
            
    except Exception as e:
        return f"⚠️ *Fallo de Conexión*\n{str(e)}\n\n_⏱️ Se autodestruirá en 5 min._"