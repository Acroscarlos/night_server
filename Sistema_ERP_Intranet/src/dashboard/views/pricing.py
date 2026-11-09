#!/usr/bin/env python
# coding: utf-8

import streamlit as st
import pandas as pd
import numpy as np
import uuid
import io
import json
import time
import requests
import os
from datetime import datetime
from pathlib import Path
from sqlalchemy.orm import joinedload
from src.database.db_core import SessionLocal
from src.database.models import Product, PricingMatrix, AuditLog
from src.logger_config import get_custom_logger
from pydantic import BaseModel, field_validator, ValidationError
from sqlalchemy.exc import IntegrityError, OperationalError
# 1. Inyección de Auditoría
pricing_logger = get_custom_logger("pricing", "modulo_pricing.log")

# --- CONFIGURACIÓN DE RUTAS PARA JSON ---
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DATA_DIR = BASE_DIR / "data"
PRICING_CONFIG_FILE = DATA_DIR / "pricing_config.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)







# ==========================================
# 🛡️ BLINDAJE DE INGESTA (PYDANTIC)
# ==========================================
class CsvImportSchema(BaseModel):
    sku: str
    nombre: str
    costo_base: float | None = None

    @field_validator('sku')
    def sanitize_sku(cls, v):
        # Elimina espacios invisibles, retornos de carro y fuerza mayúsculas
        return str(v).strip().upper()

    @field_validator('costo_base', mode='before')
    def sanitize_cost(cls, v):
        if v is None: return None
        if isinstance(v, str):
            # Transforma comas a puntos y elimina signos $ o espacios de Excel
            clean_str = v.replace(',', '.').replace('$', '').strip()
            return float(clean_str) if clean_str else 0.0
        return float(v)
# ==========================================
# UTILIDADES Y CAPA DE DATOS
# ==========================================
           
def _get_current_user() -> str:
    return st.session_state.get("username", "sistema")
def _es_admin() -> bool:
    return _get_current_user() in ["admin", "acroscarlos"]

def _delete_product(sku: str):
    """Elimina un producto de la BD (Solo Admin)."""
    db = SessionLocal()
    try:
        prod = db.query(Product).filter_by(sku=sku).first()
        if prod:
            db.delete(prod)
            db.commit()
            st.success(f"✅ Producto con SKU {sku} eliminado permanentemente.")
            return True
        else:
            st.warning("El SKU no existe en la base de datos.")
            return False
    except Exception as e:
        db.rollback()
        st.error(f"❌ Error al eliminar. Es posible que el producto ya tenga historial en compras/inventario: {e}")
        return False
    finally:
        db.close()
# --- NUEVAS FUNCIONES: PERSISTENCIA Y API ---
def _load_pricing_config() -> dict:
    """Carga los parámetros de costeo desde un JSON. Retorna valores por defecto si no existe."""
    default_config = {
        "tarifa_envio": 660.0,
        "tasa_cambio": 100.0,  # 100% significa factor 1.00 por defecto
        "comision_ml": 14.0,
        "desc_contado": 31.0,
        "desc_consignacion": 18.0
    }
    if not PRICING_CONFIG_FILE.exists():
        return default_config
    try:
        with open(PRICING_CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
            return {**default_config, **config}
    except Exception as e:
        pricing_logger.error("Error al cargar configuración de precios. Usando defaults.", exc_info=True)
        return default_config

def _save_pricing_config(config_dict: dict):
    """Guarda los parámetros de costeo de forma persistente y ATÓMICA."""
    from src.utils.json_core import SafeJSONEncoder # 🛡️ Inyectamos nuestro escudo
    
    # 1. Definimos un archivo temporal en la misma ruta
    temp_file = PRICING_CONFIG_FILE.with_suffix('.tmp')
    try:
        # 2. Escribimos al archivo temporal purgando cualquier tipo incompatible
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(config_dict, f, indent=4, cls=SafeJSONEncoder, ensure_ascii=False)
            
        # 3. Reemplazo Atómico a nivel de sistema operativo
        # Esto evita que el archivo quede en 0 bytes (corrupto) si se interrumpe el proceso
        os.replace(temp_file, PRICING_CONFIG_FILE)
        
    except Exception as e:
        pricing_logger.error(f"Error crítico guardando configuración JSON: {str(e)}", exc_info=True)
        st.error("No se pudieron guardar los parámetros de configuración de forma segura.")
        # Limpieza de emergencia: borramos el temporal si la operación falló a la mitad
        if temp_file.exists():
            os.remove(temp_file)

# 🛡️ CORTOCIRCUITO DE RED: Solo sale a internet 1 vez cada 60 segundos
@st.cache_data(ttl=60, show_spinner=False)
def _get_api_raw_json():
    """Consulta la API de forma aislada y cacheada por Streamlit."""
    # Reducimos el timeout a 5s para no penalizar la UI si la API está muy lenta
    response = requests.get("https://ve.dolarapi.com/v1/dolares", timeout=5)
    response.raise_for_status()
    return response.json()

def _fetch_dolar_api():
    """Consulta DolarAPI con circuito de contingencia (Circuit Breaker local)."""
    try:
        # Llamamos a la función cacheada en lugar de a requests directamente
        data = _get_api_raw_json()

        # 🛡️ Validar integridad estructural del JSON para evitar colapsos
        if not isinstance(data, list):
            raise ValueError("Estructura de JSON irreconocible (no es una lista)")

        oficial_rate = None
        paralelo_rate = None

        # 🎯 TU LÓGICA INTACTA: Extraemos exactamente como lo tenías
        for item in data:
            if item.get("fuente") == "oficial":
                oficial_rate = item.get("promedio")
            elif item.get("fuente") == "paralelo":
                paralelo_rate = item.get("promedio")

        # Validamos que realmente obtuvimos los números antes de retornar
        if oficial_rate is None or paralelo_rate is None:
            raise ValueError("Faltan datos de la tasa en la respuesta de la API")

        return oficial_rate, paralelo_rate

    except Exception as e:
        pricing_logger.error(f"Caída de DolarAPI o parseo fallido: {str(e)}. Activando Fallback.", exc_info=True)

        # 🛡️ FALLBACK SILENCIOSO: Extraemos las tasas de contingencia de la configuración guardada
        config_salvavidas = _load_pricing_config()
        tasa_oficial = config_salvavidas.get("tasa_oficial_ref", None)
        tasa_paralelo = config_salvavidas.get("tasa_paralelo_ref", None)

        # Avisamos al usuario con un Toast interactivo sin interrumpir su flujo de trabajo
        st.toast("⚠️ DolarAPI no responde. Usando última tasa almacenada de respaldo.", icon="💱")

        return tasa_oficial, tasa_paralelo

def _load_pricing_data() -> pd.DataFrame:
    """Carga de precios optimizada con extracción plana para evitar DetachedInstanceError."""
    db = SessionLocal()
    try:
        productos = db.query(Product).options(joinedload(Product.pricing_matrix)).all()
        data = []

        for p in productos:
            pm = p.pricing_matrix
            # 🛡️ EXTRACCIÓN PLANA: Forzamos tipos primitivos (str, float) ANTES de cerrar la BD
            data.append({
                'product_id': p.id,
                'marca': str(p.marca) if p.marca else 'General',
                'imagen_url': str(p.imagen_url) if p.imagen_url else '',
                'sku': str(p.sku),
                'nombre': str(p.nombre),
                'costo_base': float(p.costo_base or 0.0),
                'inventario_entrante': float(p.inventario_entrante or 0.0),
                'volumen_m3': float(pm.volumen_m3) if pm else 0.0,
                'envio_porcentual': float(pm.envio_porcentual) if pm else 0.0,
                'marketing_porcentual': float(pm.marketing_porcentual) if pm else 0.0,
                'margen_deseado': float(pm.margen_deseado) if pm else 0.30,
                'precio_competencia': float(pm.precio_competencia) if pm else 0.0,
            })
        return pd.DataFrame(data) if data else pd.DataFrame()
    except Exception as e:
        pricing_logger.error(f"Error cargando matriz de precios: {str(e)}", exc_info=True)
        st.error("❌ Error de base de datos. Operación cancelada.")
        return pd.DataFrame()
    finally:
        db.close() # 🛡️ Ahora es 100% seguro cerrar la sesión

def _save_draft(df: pd.DataFrame):
    db = SessionLocal()
    try:
        for _, row in df.iterrows():
            # 🛡️ ESCUDO ANTI-FANTASMAS: Verificamos que el producto aún exista en la BD
            # Esto evita el 'Red Screen of Death' si otro admin borró el producto mientras tú editabas
            # 🛡️ BLOQUEO PESIMISTA: Congela la fila en SQLite (.with_for_update()) para esta transacción
            prod_existe = db.query(Product).filter_by(id=row['product_id']).with_for_update().first()
            if not prod_existe:
                pricing_logger.warning(f"Omitiendo producto fantasma (eliminado por otro usuario): {row['product_id']}")
                continue # Saltamos este registro y salvamos el resto sin colapsar

            pm = db.query(PricingMatrix).filter_by(product_id=row['product_id']).with_for_update().first()
            if not pm:
                pm = PricingMatrix(id=str(uuid.uuid4()), product_id=row['product_id'])
                db.add(pm)
            
            pm.volumen_m3 = float(row.get('volumen_m3', 0.0))
            pm.envio_porcentual = float(row.get('envio_porcentual', 0.0))
            pm.marketing_porcentual = float(row.get('marketing_porcentual', 0.0))
            pm.margen_deseado = float(row.get('margen_deseado', 0.30))
            pm.precio_competencia = float(row.get('precio_competencia', 0.0))
            
            # 🎯 TU LÓGICA INTACTA Y OPTIMIZADA: Si es admin, actualiza el Costo MAC
            # (Reutilizamos 'prod_existe' para no saturar la base de datos con otra consulta)
            if _es_admin() and 'costo_base' in row:
                prod_existe.costo_base = float(row.get('costo_base', prod_existe.costo_base or 0.0))
                
        db.commit()
        st.success("✅ Borrador de precios guardado en la base de datos.")
        
    except IntegrityError as e:
        # 🛡️ CAPTURA DE COLISIONES
        db.rollback()
        pricing_logger.error(f"IntegrityError detectado por desincronización: {str(e)}")
        st.error("🚨 Error de concurrencia: Un producto en tu matriz fue eliminado o alterado por otro usuario. Por favor, recarga la página.")
        
    except OperationalError as e:
        # 🛡️ DEGRADACIÓN ELEGANTE Y SECOPS: Atrapa timeouts sin revelar SQL al usuario
        db.rollback()
        pricing_logger.error(f"Bloqueo de base de datos (Timeout SQLite): {e}") 
        st.error("⏳ El servidor está procesando demasiadas solicitudes en este momento. Por favor, espera unos segundos e intenta nuevamente.")
        
    except Exception as e:
        # 🛡️ ERROR GENÉRICO: Oculta el Stack Trace en la UI
        db.rollback()
        pricing_logger.error(f"Error crítico inesperado: {e}", exc_info=True)
        st.error("❌ Ocurrió un error inesperado al procesar los datos. El equipo de soporte ha sido notificado en los logs.")
        
    finally:
        db.close()

def _sync_to_catalog(df_resultante: pd.DataFrame):
    db = SessionLocal()
    user = _get_current_user()
    try:
        cambios = 0
        for _, row in df_resultante.iterrows():
            # 🛡️ BLOQUEO PESIMISTA: Evita que otro administrador edite el mismo producto en este milisegundo
            prod = db.query(Product).filter_by(id=row['product_id']).with_for_update().first()
            if prod:
                nuevo_precio = float(np.nan_to_num(row.get('Precio_PVP', 0.0)))
                
                if prod.precio_venta != nuevo_precio:
                    detalle = {
                        "sku": prod.sku, 
                        "precio_anterior": prod.precio_venta, 
                        "nuevo_precio": nuevo_precio
                    }
                    
                    prod.precio_venta = nuevo_precio
                    log = AuditLog(tabla_afectada="products", registro_id=prod.id, accion="UPDATE_PRICING", usuario=user, detalles_json=detalle)
                    db.add(log)
                    cambios += 1
                    
        db.commit()
        st.success(f"📦 Catálogo Maestro sincronizado exitosamente. ({cambios} precios actualizados)")
    except IntegrityError as e:
        # 🛡️ CAPTURA DE COLISIONES
        db.rollback()
        pricing_logger.error(f"IntegrityError detectado por desincronización: {str(e)}")
        st.error("🚨 Error de concurrencia: Un producto en tu matriz fue eliminado o alterado por otro usuario. Por favor, recarga la página.")
        
    except OperationalError as e:
        # 🛡️ DEGRADACIÓN ELEGANTE Y SECOPS: Atrapa timeouts sin revelar SQL al usuario
        db.rollback()
        pricing_logger.error(f"Bloqueo de base de datos (Timeout SQLite): {e}") 
        st.error("⏳ El servidor está procesando demasiadas solicitudes en este momento. Por favor, espera unos segundos e intenta nuevamente.")
        
    except Exception as e:
        # 🛡️ ERROR GENÉRICO: Oculta el Stack Trace en la UI
        db.rollback()
        pricing_logger.error(f"Error crítico inesperado: {e}", exc_info=True)
        st.error("❌ Ocurrió un error inesperado al procesar los datos. El equipo de soporte ha sido notificado en los logs.")
        
    finally:
        db.close()

# ==========================================
# MOTOR VECTORIZADO PANDAS
# ==========================================

def _calculate_pricing(df: pd.DataFrame, tarifa_vol: float, tasa_cambio_porcentaje: float, 
                       com_ml: float, desc_cont: float, desc_consig: float, gap_comp: float) -> pd.DataFrame:
    if df.empty: return df
    
    # 🛡️ EXPULSIÓN DE INFINITOS Y RELLENO ABSOLUTO
    d = df.replace([np.inf, -np.inf], np.nan).fillna(0).copy()

    # 🛡️ HARD CLIPPING: Protecciones físicas y matemáticas
    d['costo_base'] = np.clip(d.get('costo_base', 0), 0.0, None)
    d['volumen_m3'] = np.clip(d.get('volumen_m3', 0), 0.0, 1000.0)

    # Convertimos el porcentaje calculado de la UI (ej. 146.5%) a un multiplicador matemático (1.465)
    factor_cambio = tasa_cambio_porcentaje / 100.0

    # 1. Costo Origen (MAC original sin ajustar)
    d['Costo_Origen'] = d['costo_base']

    # 2. Costo Logística (Calculado sobre el costo puro)
    d['Costo_Logistica'] = (d['volumen_m3'] * tarifa_vol) + (d['Costo_Origen'] * d['envio_porcentual']) + (d['Costo_Origen'] * d['marketing_porcentual'])

    # 3. Costo Total Real Ajustado (Toda la importación absorbe el impacto cambiario)
    d['Costo_Total_Real'] = (d['Costo_Origen'] + d['Costo_Logistica']) * factor_cambio
    
    # --- 🛡️ BLINDAJE MATEMÁTICO: GROSS MARGIN BOTTOM-UP REAL ---
    # Limitadores (Clipping): Tope máximo estricto del 99% para evitar colapso
    margen_seguro = np.clip(d.get('margen_deseado', 0.30), 0.0, 0.99)
    com_ml_seguro = np.clip(com_ml, 0.0, 0.99)

    # ==========================================
    # 🔄 NUEVO: MOTOR DE REPRICER DINÁMICO
    # ==========================================
    # A. La Memoria Intocable (Recargo si fuéramos monopolio)
    d['Recargo_Base'] = np.where(margen_seguro < 1.0, 1 / (1 - margen_seguro), 1.0)
    
    # B. El Recargo Competitivo (Ingeniería Inversa desde el PVP de la competencia)
    precio_objetivo = np.maximum(d['precio_competencia'] - gap_comp, 0)
    ingreso_neto_necesario = precio_objetivo * (1 - com_ml_seguro)
    recargo_competitivo = np.where(d['Costo_Total_Real'] > 0, ingreso_neto_necesario / d['Costo_Total_Real'], 0)

    # C. Decisión: ¿Hay competencia? (Libertad total de recargo, sin pisos forzados)
    d['Recargo_Aplicado'] = np.where(d['precio_competencia'] > 0, recargo_competitivo, d['Recargo_Base'])

    # Creamos un denominador seguro para evitar advertencias de división por cero en Pandas
    recargo_seguro = np.where(d['Recargo_Aplicado'] == 0, 1.0, d['Recargo_Aplicado'])
    d['Margen_Real_%'] = np.where(d['Recargo_Aplicado'] > 0, (1 - (1 / recargo_seguro)) * 100, 0)

    # np.where actúa como firewall: si el costo es <= 0, el precio es 0 (evita Infinitos)
    d['Ingreso_Neto'] = np.where(d['Costo_Total_Real'] > 0, d['Costo_Total_Real'] * d['Recargo_Aplicado'], 0)

    # Gross-Up PVP a prueba de balas (Con protección de Zero-Division del Auditor)
    d['Precio_PVP'] = np.where(
        (d['Ingreso_Neto'] > 0) & (com_ml_seguro < 1.0),
        d['Ingreso_Neto'] / (1 - com_ml_seguro),
        0
    )
    
    # NUEVO: Precio en Divisas (Con factor de recargo en efectivo 3.5%)
    d['Precio_Divisas'] = np.where(factor_cambio > 0, (d['Precio_PVP'] / factor_cambio) * 1.035, 0)
    
    d['Precio_Aliado_Contado'] = d['Precio_PVP'] * (1 - desc_cont)
    d['Precio_Aliado_Consig'] = d['Precio_PVP'] * (1 - desc_consig)

    # 4. Estatus Competencia (Transformado a Semáforo de Rentabilidad)
    conditions = [
        d['Recargo_Aplicado'] < 1.5,
        (d['Recargo_Aplicado'] >= 1.5) & (d['Recargo_Aplicado'] <= 2.0),
        d['Recargo_Aplicado'] > 2.0
    ]
    choices = [
        "🔴 CRÍTICO (<1.5)", 
        "🟡 AJUSTADO (1.5 - 2.0)", 
        "✅ ÓPTIMO (>2.0)"
    ]
    d['Estatus_Competencia'] = np.select(conditions, choices, default="🔵 INDETERMINADO")

    # ==========================================
    # ?? NUEVO: REDONDEO FINANCIERO ESTRATÉGICO
    # ==========================================
    # 1. Costos Internos a 2 decimales (Mantiene la contabilidad exacta)
    cols_costos = ['Costo_Origen', 'Costo_Logistica', 'Costo_Total_Real', 'Ingreso_Neto', 'Precio_Divisas']
    for col in cols_costos:
        if col in d.columns:
            d[col] = d[col].round(2)

    # 2. Precios de Venta a 0 decimales (Seguridad Anti-Overflow)
    cols_precios = ['Precio_PVP', 'Precio_Aliado_Contado', 'Precio_Aliado_Consig']
    
    # 🛡️ PARACAÍDAS NIVEL 2: Ningún precio puede ser menor al costo base absoluto (salvavidas comercial)
    costo_minimo = np.maximum(d.get('costo_base', 0.01), 0.01)

    for col in cols_precios:
        if col in d.columns:
            # 🛡️ HARD CEILING: Límite de 99 Millones para evitar Integer Overflows
            d[col] = np.clip(d[col], a_min=costo_minimo, a_max=99999999.0)
            
            # NUNCA usar .astype(int). Usamos np.round para mantener el Float64 a salvo
            d[col] = np.round(d[col], 0)

    d['Recargo_Aplicado'] = d['Recargo_Aplicado'].round(2)
    d['Margen_Real_%'] = d['Margen_Real_%'].round(2)

    return d

# ==========================================
# VISTA PRINCIPAL STREAMLIT
# ==========================================
@st.dialog("⚠️ Resolver Conflictos de SKU (Admin)", width="large")
def _resolver_conflictos_importacion(lista_nuevos: list, lista_conflictos: list):
    """Interfaz modal para auditar y resolver conflictos de importación de catálogo."""
    st.info(f"Se detectaron {len(lista_conflictos)} SKUs que ya existen en la base de datos.")

    df_conflictos = pd.DataFrame(lista_conflictos)

    # Renderizar el editor interactivo con bloqueos de seguridad
    edited_df = st.data_editor(
        df_conflictos,
        hide_index=True,
        width="stretch",
        disabled=["SKU", "Nombre Actual", "Costo Actual", "Nuevo Nombre", "Nuevo Costo"],
        column_config={
            "Sobrescribir ✅": st.column_config.CheckboxColumn("Sobrescribir ✅", default=True),
            "Costo Actual": st.column_config.NumberColumn("Costo Actual", format="$%.2f"),
            "Nuevo Costo": st.column_config.NumberColumn("Nuevo Costo", format="$%.2f")
        }
    )

    st.divider()
    if st.button("💾 Confirmar Importación y Actualización", type="primary", width="stretch"):
        db = SessionLocal()
        try:
            # 1. Inserción de SKUs totalmente nuevos
            for item in lista_nuevos:
                nuevo_prod = Product(
                    id=str(uuid.uuid4()),
                    sku=item['SKU'],
                    nombre=item['Nuevo Nombre']
                )
                if pd.notna(item['Nuevo Costo']):
                    nuevo_prod.costo_base = float(item['Nuevo Costo'])
                db.add(nuevo_prod)

            # 2. Actualización (UPDATE) selectiva de SKUs con conflicto
            procesados_act = 0
            for index, row in edited_df.iterrows():
                if row.get('Sobrescribir ✅', False):
                    prod_existente = db.query(Product).filter_by(sku=row['SKU']).first()
                    if prod_existente:
                        prod_existente.nombre = row['Nuevo Nombre']
                        if pd.notna(row['Nuevo Costo']):
                            prod_existente.costo_base = float(row['Nuevo Costo'])
                        procesados_act += 1

            db.commit()
            st.success(f"✅ Éxito: {len(lista_nuevos)} creados y {procesados_act} actualizados.")
            if "pricing_raw_df" in st.session_state: del st.session_state["pricing_raw_df"]
            time.sleep(1.5)
            st.rerun()

        except Exception as e:
            db.rollback()
            pricing_logger.error(f"Fallo crítico durante importación con conflictos: {str(e)}", exc_info=True)
            st.error(f"Ocurrió un error al guardar en la base de datos: {e}")
        finally:
            db.close()








# ==========================================
# SOLUCIÓN UX - MODAL DE CONFIRMACIÓN DE BORRADO
# ==========================================
@st.dialog("⚠️ Confirmar Acción Destructiva")
def modal_confirmar_borrado(sku, nombre):
    st.error(f"Estás a punto de eliminar permanentemente el producto **{nombre}** (SKU: {sku}).")
    st.markdown("¿Estás completamente seguro? Esta acción **no se puede deshacer** y borrará su historial de la matriz de precios.")
    
    col1, col2 = st.columns(2)
    if col1.button("❌ Cancelar", width='stretch'):
        st.rerun() # Cierra el modal sin hacer nada
        
    if col2.button("🗑️ Sí, Eliminar", type="primary", width='stretch'):
        # Llama a tu función original de borrado
        if _delete_product(sku):
            # Limpia la caché para que la tabla se actualice sin el producto borrado
            if "pricing_raw_df" in st.session_state: 
                del st.session_state["pricing_raw_df"]
            time.sleep(1.5)
            st.rerun()



# ==========================================
# NUEVO: MODAL DE FRICCIÓN PARA DOLAR API
# ==========================================
@st.dialog("💱 Confirmar Tasa de Cambio (DolarAPI)")
def modal_dolar_api(config_actual: dict):
    """Modal de friccion positiva para evitar cambios financieros accidentales."""
    try:
        with st.spinner("Consultando DolarAPI..."):
            oficial, paralelo = _fetch_dolar_api()
            
        if oficial is None or paralelo is None:
            st.error("🚨 Error: DolarAPI fallo o devolvio un formato irreconocible.")
            return
            
        # Calculo estricto de la brecha cambiaria
        nueva_tasa = round((paralelo / oficial) * 100, 2)
        tasa_vieja = round(config_actual.get("tasa_cambio", 100.0), 2)
        
        # --- UI DEL MODAL ---
        st.markdown("### Resultados del Calculo")
        col_old, col_new = st.columns(2)
        col_old.metric("Tasa Actual Guardada", f"{tasa_vieja}%")
        col_new.metric("Tasa Sugerida (Brecha)", f"{nueva_tasa}%", delta=f"{round(nueva_tasa - tasa_vieja, 2)}%")
        
        tasa_final_a_guardar = nueva_tasa
        
        # 🛡️ ADMIN OVERRIDE (DENTRO DEL MODAL)
        if _es_admin():
            st.info("🛠️ **Modo Admin:** Puedes redondear o ajustar la tasa sugerida antes de guardarla.")
            tasa_final_a_guardar = st.number_input(
                "Sobrescribir Tasa (%)", 
                value=float(nueva_tasa), 
                step=1.0, 
                format="%.2f"
            )
            
        # --- BOTONES DE DECISION ---
        st.divider()
        c1, c2 = st.columns(2)
        if c1.button("✅ Confirmar y Actualizar", use_container_width=True, type="primary"):
            config_actual["tasa_cambio"] = tasa_final_a_guardar
            config_actual["tasa_oficial_ref"] = oficial
            config_actual["tasa_paralelo_ref"] = paralelo
            _save_pricing_config(config_actual)
            st.success("Tasa actualizada y asegurada.")
            time.sleep(1)
            st.rerun()
            
        if c2.button("❌ Mantener Anterior", use_container_width=True):
            st.rerun()
            
    except Exception as e:
        st.error(f"Fallo al ejecutar la validacion: {str(e)}")


def _execute_save_matrix(df_proy):
    """Función de extracción para guardar CSV (usable en modales)."""
    with st.spinner("Guardando en el servidor..."):
        try:
            from pathlib import Path
            BASE_DIR_LOCAL = Path(__file__).resolve().parent.parent.parent.parent
            output_dir = BASE_DIR_LOCAL / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            ruta_csv = output_dir / "matrix_de_precios.csv"
            df_proy.to_csv(ruta_csv, index=False, encoding='utf-8-sig')
            st.success("✅ Matriz lista para sincronización")
            st.toast("📂 Guardado como matrix_de_precios.csv", icon="💾")
        except Exception as e:
            st.error(f"❌ Error al guardar el archivo: {e}")

@st.dialog("🔴 Alerta de Rentabilidad Crítica", width="large")
def modal_alerta_rentabilidad(df_critico: pd.DataFrame, df_proyectado: pd.DataFrame, accion: str):
    st.error(f"⚠️ Estás a punto de **{accion}** el sistema con {len(df_critico)} productos por debajo del umbral mínimo de rentabilidad (Recargo < 1.5).")
    st.markdown("### 📊 Productos en Riesgo (Revisión Obligatoria)")

    cols_mostrar = ['sku', 'nombre', 'Costo_Total_Real', 'precio_competencia', 'Recargo_Aplicado', 'Margen_Real_%', 'Precio_PVP']
    st.dataframe(
        df_critico[cols_mostrar],
        hide_index=True,
        column_config={
            "Costo_Total_Real": st.column_config.NumberColumn("Costo Real", format="$%.2f"),
            "precio_competencia": st.column_config.NumberColumn("Competencia", format="$%.2f"),
            "Precio_PVP": st.column_config.NumberColumn("PVP Propuesto", format="$%.2f"),
            "Recargo_Aplicado": st.column_config.NumberColumn("Recargo 🔴", format="%.2f"),
            "Margen_Real_%": st.column_config.NumberColumn("Margen (%) 🔴", format="%.2f%%")
        }
    )

    st.info("¿Estás completamente seguro de que deseas absorber este riesgo y aplicar estos precios?")
    col1, col2 = st.columns(2)
    if col1.button("❌ Cancelar y Revisar", width="stretch"):
        st.rerun()

    import time # Asegúrate de que esto esté arriba en tus importaciones globales

    # 🛡️ 1. EVALUACIÓN Y AUTO-SANACIÓN DEL CANDADO
    lock_activo = False
    if "action_lock_expires" in st.session_state:
        if time.time() < st.session_state.action_lock_expires:
            lock_activo = True # El candado sigue vigente
        else:
            # TTL Vencido: El sistema se recupera de un crash anterior
            del st.session_state["action_lock_expires"]
            lock_activo = False

    # 🛡️ 2. FUNCIÓN DE BLOQUEO (Otorga 30 segundos de protección)
    def lock_action_ttl():
        st.session_state.action_lock_expires = time.time() + 30.0

    # 3. RENDERIZADO DEL BOTÓN
    btn_confirm = col2.button(
        f"✅ Sí, Absorber Riesgo y {accion}", 
        type="primary", 
        width="stretch",
        disabled=lock_activo,
        on_click=lock_action_ttl
    )

    # 4. EJECUCIÓN
    if btn_confirm:
        try:
            if accion == "Sincronizar":
                _sync_to_catalog(df_proyectado)
            elif accion == "Guardar Matriz":
                _execute_save_matrix(df_proyectado)
                
            if "pricing_raw_df" in st.session_state:
                del st.session_state["pricing_raw_df"]
                
            time.sleep(1.5)
        finally:
            # 🛡️ LIBERACIÓN FORZADA LIMPIA
            if "action_lock_expires" in st.session_state:
                del st.session_state["action_lock_expires"]
            st.rerun()


def render_pricing_view():
    st.markdown("### 🧠 Módulo de Precios Inteligentes")
    
    # --- 1. PARÁMETROS GLOBALES (PERSISTENTES Y CONECTADOS A API) ---
    config = _load_pricing_config()
    
    with st.expander("⚙️ Parametros Globales de Costeo", expanded=True):
        
        # --- BLOQUE ROBUSTO CON ADMIN OVERRIDE ---
        es_administrador = _es_admin()
        
        col_api1, col_api2, col_api3 = st.columns([2, 2, 1])
        
        with col_api1:
            if st.button("🌐 Calcular Tasa (DolarAPI)", use_container_width=True):
                # Esto invoca la Friccion Positiva (Modal) que inyectamos arriba
                modal_dolar_api(config)
                
        with col_api2:
            # Mostramos las tasas de referencia si existen en la memoria JSON
            if "tasa_oficial_ref" in config and "tasa_paralelo_ref" in config:
                st.info(f"💡 Ref DolarAPI: Oficial **{config['tasa_oficial_ref']} Bs** | Paralelo **{config['tasa_paralelo_ref']} Bs**")
                
        st.divider()
        
        # --- BLOQUE INPUTS CON VARIABLES PERSISTENTES ---
        col1, col2, col3, col_gap, col4, col5 = st.columns(6)
        
        tarifa_vol = col1.number_input(
            "Tarifa Envío ($/m3)", 
            value=float(config.get("tarifa_envio", 660.0)), step=10.0,
            help="🚢 Matemática: Multiplica el volumen de cada caja por esta tarifa para obtener el costo de flete principal.\n\n💡 Sugerido: Revisa tu último conocimiento de embarque marítimo."
        )
        
        tasa_cambio = col2.number_input(
            "Tasa de Cambio (%)", 
            value=float(config.get("tasa_cambio", 100.0)), step=0.01, disabled=not es_administrador,
            help="💱 Matemática: [Costo MAC × (Tasa / 100)]. Ajusta el valor de la mercancía si el dólar paralelo se separa del oficial.\n\n💡 Nota: Se calcula automáticamente vía DolarAPI."
        )
        
        val_ml = col3.number_input(
            "Comisión MercadoLibre (%)", 
            value=float(config.get("comision_ml", 14.0)), step=1.0,
            help="🤝 Matemática: Factoriza el PVP hacia arriba (Gross-Up) para absorber el costo de venta y no sacrificar tu ingreso.\n\n💡 Sugerido: 11% (Clásica) a 15% (Premium) según tu publicación."
        )
        comision_ml = val_ml / 100.0
        
        # 🆕 NUEVA CAJA: GAP COMPETITIVO
        gap_comp = col_gap.number_input(
            "Gap Comp. ($)", 
            value=float(config.get("gap_competitivo", 0.50)), step=0.10,
            help="🔪 Matemática: Cuántos dólares por debajo de la competencia quieres fijar tu PVP."
        )
        
        val_contado = col4.number_input(
            "Desc. Aliado Contado (%)", 
            value=float(config.get("desc_contado", 31.0)), step=1.0,
            help="🚚 Matemática: [Precio PVP × (1 - Descuento %)].\n\n💡 Sugerido: 25% a 35% para que tu distribuidor tenga un margen atractivo al revender pagando de inmediato."
        )
        desc_contado = val_contado / 100.0
        
        val_consig = col5.number_input(
            "Desc. Aliado Consign. (%)", 
            value=float(config.get("desc_consignacion", 18.0)), step=1.0,
            help="📦 Matemática: [Precio PVP × (1 - Descuento %)].\n\n💡 Sugerido: 15% a 20%. Es menor al de contado porque tú asumes el riesgo de tener inventario inmovilizado."
        )
        desc_consig = val_consig / 100.0

        if st.button("💾 Guardar Parámetros", type="primary"):
            nuevos_params = {
                "tarifa_envio": tarifa_vol,
                "tasa_cambio": config.get("tasa_cambio", 100.0), # Rescatamos la tasa del config para no perderla
                "comision_ml": val_ml,
                "gap_competitivo": gap_comp, # 🆕 GUARDAMOS EL GAP EN EL JSON
                "desc_contado": val_contado,
                "desc_consignacion": val_consig,
                "tasa_oficial_ref": config.get("tasa_oficial_ref"),
                "tasa_paralelo_ref": config.get("tasa_paralelo_ref")
            }
            _save_pricing_config(nuevos_params)
            st.success("✅ Parámetros globales guardados exitosamente.")
            time.sleep(1)
            st.rerun()

    st.markdown("---")
    
    # ==========================================
    # NUEVO: IMPORTADOR DE PRECIOS CSV (REGEX ARMOR Y REGLA ESTRICTA)
    # ==========================================
    import re
    
    def sanitize_european_currency(value) -> float:
        """Parsea monedas complejas con Regex ($5.849,35 -> 5849.35)."""
        if pd.isna(value): return 0.0
        val_str = str(value).strip().lower()

        # 🛡️ Detección de notación científica nativa (ej. 1.5e3 -> 1500)
        if 'e' in val_str:
            try: return float(val_str)
            except ValueError: return 0.0

        # Limpieza de símbolos, dejando números, comas, puntos y signos negativos
        clean_str = re.sub(r'[^\d.,-]', '', val_str)
        if not clean_str: return 0.0

        # Manejo heurístico de formato europeo vs americano
        if '.' in clean_str and ',' in clean_str:
            if clean_str.rfind(',') > clean_str.rfind('.'): # Ej: 1.000.000,50
                clean_str = clean_str.replace('.', '').replace(',', '.')
            else: # Ej: 1,000,000.50
                clean_str = clean_str.replace(',', '')
        elif ',' in clean_str: # Ej: 65,00
            clean_str = clean_str.replace(',', '.')

        try:
            return float(clean_str)
        except ValueError:
            return 0.0

    with st.expander("📥 Importar Lista de Precios Base (CSV)", expanded=False):
        st.info("💡 Solo se actualizarán los precios de los SKUs que ya existan en la base de datos local. Los títulos se mantendrán intactos.")
        uploaded_file = st.file_uploader("Sube un archivo CSV con columnas 'SKU' y 'Costo'", type=['csv'])
        
        if uploaded_file is not None:
            if st.button("🚀 Iniciar Importación de Precios MAC", type="primary", width="stretch"):
                try:
                    df_import = pd.read_csv(uploaded_file)
                    df_import.columns = df_import.columns.str.strip().str.upper()
                    
                    if 'SKU' not in df_import.columns:
                        st.error("🚨 El archivo debe contener la columna 'SKU'.")
                    else:
                        # Autodetectar la columna de costo (acepta "COSTO", "COST", "PRECIO", etc.)
                        col_costo = next((col for col in df_import.columns if 'COSTO' in col or 'COST' in col or 'PRECIO' in col), None)
                                
                        if not col_costo:
                            st.error("🚨 No se encontró una columna de costo (Busca 'Costo', 'Costo Base', etc.)")
                        else:
                            with st.spinner("Procesando precios y aplicando Regex..."):
                                df_import['COSTO_LIMPIO'] = df_import[col_costo].apply(sanitize_european_currency)
                                
                                db = SessionLocal()
                                actualizados, ignorados, errores_formato = 0, 0, 0
                                
                                try:
                                    for _, row in df_import.iterrows():
                                        sku_limpio = str(row['SKU']).strip() if pd.notna(row['SKU']) else None
                                        nuevo_costo = row['COSTO_LIMPIO']

                                        if not sku_limpio or sku_limpio.lower() == 'nan':
                                            continue
                                            
                                        if nuevo_costo == 0.0 and str(row[col_costo]).strip() not in ["0", "0.0"]:
                                            errores_formato += 1
                                            continue

                                        # Regla de negocio: Solo actualiza si existe
                                        prod = db.query(Product).filter(Product.sku == sku_limpio).first()
                                        if prod:
                                            prod.costo_base = nuevo_costo
                                            actualizados += 1
                                        else:
                                            ignorados += 1

                                    db.commit()
                                    st.success(f"✅ Catálogo actualizado: {actualizados} precios modificados.")
                                    if ignorados > 0:
                                        st.warning(f"⚠️ {ignorados} SKUs ignorados (no existen en la base de datos local).")
                                    if errores_formato > 0:
                                        st.error(f"❌ {errores_formato} filas ignoradas por formato numérico incomprensible.")
                                        
                                    if "pricing_raw_df" in st.session_state:
                                        del st.session_state["pricing_raw_df"]
                                    time.sleep(2)
                                    st.rerun()

                                except Exception as e:
                                    db.rollback()
                                    st.error(f"❌ Error fatal procesando base de datos: {e}")
                                finally:
                                    db.close()
                except Exception as e:
                    st.error(f"Error leyendo archivo CSV: {e}")
                
    st.markdown("---")

    # Cargar Data (Memoria en sesión)
    if "pricing_raw_df" not in st.session_state:
        st.session_state.pricing_raw_df = _load_pricing_data()











    df_raw = st.session_state.pricing_raw_df
    if df_raw.empty:
        st.warning("No hay productos en el catálogo maestro. Crea productos primero.")
        return

    # ==========================================
    # NUEVO: EXTRACCIÓN DINÁMICA DE MARCA (1ra Palabra)
    # ==========================================
    # Tomamos el nombre, lo convertimos a string, lo dividimos por espacios y tomamos el elemento [0]
    df_raw['marca_dinamica'] = df_raw['nombre'].astype(str).apply(
        lambda x: x.strip().split()[0].upper() if pd.notna(x) and x.strip() else "GENERAL"
    )

    # ==========================================
    # BUSCADOR Y FILTROS GLOBALES
    # ==========================================
    st.markdown("#### 🔍 Filtros Globales de Catálogo")
    col_search, col_brand = st.columns(2)

    with col_search:
        search_query = st.text_input("Buscador", placeholder="🔎 Buscar por SKU o Nombre...", key="ui_search_query").strip().lower()

    marcas_disponibles = sorted(df_raw['marca_dinamica'].unique().tolist())
    with col_brand:
        marcas_seleccionadas = st.multiselect("Filtrar por Marca", options=marcas_disponibles, default=marcas_disponibles, key="ui_marcas_seleccionadas")

    df_filtered = df_raw.copy()
    if search_query:
        df_filtered = df_filtered[
            df_filtered['sku'].astype(str).str.lower().str.contains(search_query, na=False) |
            df_filtered['nombre'].astype(str).str.lower().str.contains(search_query, na=False)
        ]

    if marcas_seleccionadas:
        df_filtered = df_filtered[df_filtered['marca_dinamica'].isin(marcas_seleccionadas)]

    if df_filtered.empty:
        st.info("No se encontraron productos con estos filtros.")
        return
        
    st.markdown("---")
































    # ==========================================
    # PESTAÑAS DINÁMICAS Y TABLA REACTIVA
    # ==========================================
    # ==========================================
    # 🎯 RENDERIZADO LAZY Y TABLA REACTIVA (O(1) en RAM)
    # ==========================================
    marcas = df_filtered['marca_dinamica'].unique().tolist()
    if not marcas: marcas = ["GENERAL"]
    
    st.markdown("#### 🎯 Selección de Marca para Edición")
    # El usuario elige UNA sola marca a la vez para editar
    marca_activa = st.selectbox("Seleccione la marca a visualizar/editar:", options=marcas, key="ui_marca_activa")
    
    # Configuración dinámica de columnas bloqueadas
    disabled_cols = ["product_id", "marca_dinamica", "marca", "sku", "nombre", "PVP_Virtual"]
    if not _es_admin():
        disabled_cols.append("costo_base")

    editor_config = {
        "product_id": None, 
        "marca": None, 
        "marca_dinamica": None,
        "sku": st.column_config.TextColumn("SKU", disabled=True),
        "nombre": st.column_config.TextColumn("Producto", disabled=True),
        "costo_base": st.column_config.NumberColumn("Costo MAC ($)", format="$%.2f"),
        "inventario_entrante": st.column_config.NumberColumn("Entrantes", disabled=True),
        
        "margen_deseado": None, # Oculto
        
        # 🎯 Columna de entrada
        "Recargo_UI": st.column_config.NumberColumn(
            "Recargo (Multiplicador) 🎯", 
            min_value=1.01, step=0.01, format="%.2f"
        ),

        # 📊 Margen calculado al lado del recargo
        "Margen_Visual": st.column_config.NumberColumn(
            "Margen Real (%) 📈", 
            format="%.2f%%", 
            disabled=True
        ),
        
        "precio_competencia": st.column_config.NumberColumn("Precio Comp.", format="$%.2f"),
        "volumen_m3": st.column_config.NumberColumn("Volumen m³", format="%.4f"),
        "envio_porcentual": st.column_config.NumberColumn("% Gastos Extras", format="%.2f"),
        "marketing_porcentual": st.column_config.NumberColumn("Marketing (%)", format="%.2f"),
        "PVP_Virtual": st.column_config.NumberColumn("PVP Proyectado 🔒", format="$%.2f")
    }

    st.markdown("#### 1. Matriz de Entrada (Borrador)")
    if _es_admin():
        st.caption("🛡️ Privilegios de Administrador: Tienes permitido editar manualmente el **Costo MAC ($)**.")
    
    # Variables globales preparadas para la matemática superficial
    factor_cambio = tasa_cambio / 100.0
    com_ml_seguro = np.clip(comision_ml, 0.0, 0.99)

    # 1. Filtramos y copiamos SOLO el fragmento necesario para la UI
    df_marca = df_filtered[df_filtered['marca_dinamica'] == marca_activa].copy()
            
    if 'imagen_url' in df_marca.columns:
        df_marca = df_marca.drop(columns=['imagen_url'])
        
    # ==========================================
    # 🔄 1. TRADUCCIÓN HACIA LA UI (Lectura)
    # ==========================================
    df_marca['Recargo_UI'] = np.where(
        df_marca['margen_deseado'] < 1.0,
        1 / (1 - df_marca['margen_deseado']),
        1.0
    )
    df_marca['Margen_Visual'] = (1 - (1 / df_marca['Recargo_UI'])) * 100

    # Reordenar columnas
    cols_pref = ['sku', 'nombre', 'costo_base', 'inventario_entrante', 'Recargo_UI', 'Margen_Visual', 'precio_competencia']
    cols_rest = [c for c in df_marca.columns if c not in cols_pref]
    df_marca = df_marca[cols_pref + cols_rest]

    # 🚀 CÁLCULO SUPERFICIAL (Aislado y limpio)
    c_origen = df_marca.get('costo_base', 0.0)
    c_logistica = (df_marca.get('volumen_m3', 0.0) * tarifa_vol) + (c_origen * df_marca.get('envio_porcentual', 0.0)) + (c_origen * df_marca.get('marketing_porcentual', 0.0))
    
    c_total_real = (c_origen + c_logistica) * factor_cambio
    
    # 🎯 Nueva matemática de Ingreso Neto superficial (usando el Recargo)
    ingreso_neto = c_total_real * df_marca['Recargo_UI']
    df_marca['PVP_Virtual'] = np.where(ingreso_neto > 0, ingreso_neto / (1 - com_ml_seguro), 0)
        

















    # 2. Renderizamos UN SOLO data_editor (Ahorro del 99% de Memoria RAM)
    import hashlib # Asegúrate de tener esto en tus imports globales arriba

    # 🛡️ HASHING DE UI: Convierte cualquier string en un ID seguro de 32 caracteres
    safe_hash = hashlib.md5(marca_activa.encode('utf-8')).hexdigest()
    editor_key = f"editor_unico_{safe_hash}"

    # 2. Renderizamos UN SOLO data_editor (Ahorro del 99% de Memoria RAM)
    edited_df = st.data_editor(
        df_marca,
        key=editor_key, # Usamos la llave hasheada
        width="stretch",
        hide_index=True,
        disabled=disabled_cols,
        column_config=editor_config
    )
    
    # ==========================================
    # 🛡️ 2. MUTACIÓN DE MEMORIA EN TIEMPO REAL (El Seguro Anti-Pérdidas)
    # ==========================================
    if not edited_df.empty and 'product_id' in st.session_state.pricing_raw_df.columns:
        
        # 1. Traducción matemática inversa
        if 'Recargo_UI' in edited_df.columns:
            edited_df['margen_deseado'] = np.where(
                edited_df['Recargo_UI'] > 1.0,
                np.round(1 - (1 / edited_df['Recargo_UI']), 6), # 🛡️ ESTABILIZACIÓN IEEE 754
                0.0
            )

        # 2. Columnas permitidas
        cols_editables = ['margen_deseado', 'precio_competencia']
        if _es_admin():
            cols_editables.append('costo_base')

        # 3. Alineación y actualización en caliente
        df_master_idx = st.session_state.pricing_raw_df.set_index('product_id')
        edited_df_idx = edited_df.set_index('product_id')

        df_master_idx.update(edited_df_idx[cols_editables])

        # 4. Sobrescribimos la sesión global. (Esto REEMPLAZA al viejo df_consolidado)
        st.session_state.pricing_raw_df = df_master_idx.reset_index()

    st.markdown("---")

    # --- PERSISTENCIA BORRADOR A BASE DE DATOS ---
    if st.button("💾 Guardar Matriz en Servidor", type="primary", help="Asegura tu progreso enviando la memoria actual a la Base de Datos."):
        with st.spinner("Guardando en base de datos..."):
            # Pasamos directamente la memoria global, ya no usamos df_consolidado
            _save_draft(st.session_state.pricing_raw_df)
            st.success("✅ Trabajo guardado exitosamente en el servidor.")
            time.sleep(1)
            st.rerun()

    st.markdown("---")











    # --- MOTOR DE CÁLCULO Y RESULTADOS ---
    st.markdown("#### 2. Proyección y Simulador de Rentabilidad")
    # --- ZONA DE PELIGRO: ELIMINAR ITEM (SOLO ADMIN) ---
    if _es_admin():
        with st.expander("🛠️ Zona de Peligro: Eliminar Producto (Admin)", expanded=False):
            st.error("⚠️ Eliminar un producto borrará su historial de la matriz de precios.")
            lista_skus = [""] + df_raw['sku'].tolist() if not df_raw.empty else [""]
            sku_to_delete = st.selectbox("Selecciona el SKU a eliminar:", lista_skus)
            
            if st.button("🗑️ Eliminar Producto Definitivamente", type="primary"):
                if sku_to_delete:
                    # Extraer el nombre del producto para mostrarlo en el modal
                    nombre_prod = df_raw.loc[df_raw['sku'] == sku_to_delete, 'nombre'].values[0]
                    # Disparar el modal de confirmación
                    modal_confirmar_borrado(sku_to_delete, nombre_prod)
                else:
                    st.warning("⚠️ Por favor, selecciona un SKU válido de la lista.")

    # --- DESPLEGABLE CON FÓRMULAS EDUCATIVAS ---
    with st.expander("ℹ️ ¿Cómo se calculan estos precios? (Ver Fórmulas)", expanded=False):
        st.markdown("""
        **1. Costos Base (Origen y Logística pura)** `Costo Origen = Costo MAC`  
        `Costo Logística = (Volumen m3 × Tarifa Envío Marítimo) + (Costo Origen × % Gastos Extras) + (Costo Origen × Marketing %)`
        
        **2. Costo Puesto en Tienda Ajustado (Costo Total Real)** `Costo Total Real = (Costo Origen + Costo Logística) × (Tasa de Cambio % / 100)`  
        *(Ajusta el valor total de la mercancía y su importación si el dólar comercial se separa del oficial, protegiendo el pago en divisas a la agencia y al fabricante).*
        
        **3. Ingreso Neto Deseado (Vía Multiplicador de Recargo)** `Ingreso Neto = Costo Total Real × Recargo Aplicado`  
        *(El Recargo es un multiplicador comercial directo. Por ejemplo, aplicar un recargo de **1.43** equivale exactamente a obtener un **Margen de Ganancia Real del 30%** libre al banco. La relación matemática es: Margen = 1 - (1 / Recargo)).*
        
        **4. Precio Venta al Público (Gross-Up Comisiones)** `Precio PVP = Ingreso Neto / (1 - Comisión MercadoLibre %)`  
        *(Añade el impuesto de la plataforma por encima, para que al restarlo quede el Ingreso Neto intacto).*
        
        **5. Precios a Distribuidores (Aliados)** `Precio Aliado = Precio PVP × (1 - Descuento Aliado %)`
        """)
    df_proyectado = _calculate_pricing(
        st.session_state.pricing_raw_df, tarifa_vol, tasa_cambio, comision_ml, desc_contado, desc_consig, gap_comp
    )

    cols_resultado = [
        'sku', 'nombre', 'Costo_Total_Real', 
        'Recargo_Aplicado', 'Margen_Real_%',
        'Ingreso_Neto', 'Precio_PVP', 'Precio_Divisas', 'Precio_Aliado_Contado', 
        'Precio_Aliado_Consig', 'precio_competencia', 'Estatus_Competencia'
    ]
    
    st.dataframe(
        df_proyectado[cols_resultado],
        width='stretch', # Usando el parámetro moderno recomendado
        hide_index=True,
        column_config={
            "Costo_Total_Real": st.column_config.NumberColumn(
                "Costo Real ($)", 
                format="$ %.2f",
                help="💸 Suma del Costo MAC Ajustado + Flete Marítimo + Gastos Extras + Marketing. (Lo que realmente te cuesta ponerlo en el estante)."
            ),
            # ==========================================
            # 🎯 NUEVAS COLUMNAS FORMATEADAS A 2 DECIMALES
            # ==========================================
            "Recargo_Aplicado": st.column_config.NumberColumn(
                "Recargo", 
                format="%.2f",
                help="Multiplicador aplicado sobre el costo real."
            ),
            "Margen_Real_%": st.column_config.NumberColumn(
                "Margen Real (%)", 
                format="%.2f%%",
                help="Porcentaje de ganancia libre de costos."
            ),
            # ==========================================
            "Ingreso_Neto": st.column_config.NumberColumn(
                "Ingreso Neto ($)", 
                format="$ %.2f",
                help="🏦 Lo que necesitas cobrar libre de comisiones para garantizar tu Margen Deseado."
            ),
            "Precio_PVP": st.column_config.NumberColumn(
                "Precio PVP Final", 
                format="$ %.2f",
                help="🛒 Matemática: [Ingreso Neto / (1 - Comisión ML %)]. Precio al público con el impuesto de plataforma ya sumado por encima (Gross-Up)."
            ),

            "Precio_Divisas": st.column_config.NumberColumn(
                "PVP Divisa ($)", 
                format="$ %.2f",
                help="💵 Matemática: [(PVP Proyectado / Tasa Cambio) * 1.035]. Precio aplicable para pagos en efectivo físico (Incluye 3.5% recargo)."
            ),

            "Precio_Aliado_Contado": st.column_config.NumberColumn(
                "P. Contado", 
                format="$ %.2f",
                help="Precio sugerido para mayoristas que pagan de contado."
            ),
            "Precio_Aliado_Consig": st.column_config.NumberColumn(
                "P. Consign.", 
                format="$ %.2f",
                help="Precio sugerido para aliados que reciben mercancía a crédito/consignación."
            ),
            "precio_competencia": st.column_config.NumberColumn(
                "P. Competencia", 
                format="$ %.2f"
            ),
            "Estatus_Competencia": st.column_config.TextColumn(
                "Estatus 🚦",
                help="Compara tu Precio PVP Final contra el Precio de la Competencia."
            )
        }
    )

    # --- SINCRONIZACIÓN Y EXPORTACIÓN ---
    st.markdown("---")
    
    # 🕵️ Filtramos si existe algún producto con bandera roja
    df_critico = df_proyectado[df_proyectado['Recargo_Aplicado'] < 1.5]
    
    c_sync, c_csv, c_excel = st.columns(3)
    
    with c_sync:
        if st.button("🚀 Sincronizar al Catálogo Maestro", type="primary", width="stretch"):
            if not df_critico.empty:
                # Disparamos la ventana emergente
                modal_alerta_rentabilidad(df_critico, df_proyectado, "Sincronizar")
            else:
                _sync_to_catalog(df_proyectado)

    with c_csv:
        if st.button("💾 Guardar Matriz (Cashea/Woo)", width="stretch"):
            if not df_critico.empty:
                # Disparamos la ventana emergente
                modal_alerta_rentabilidad(df_critico, df_proyectado, "Guardar Matriz")
            else:
                _execute_save_matrix(df_proyectado)

    with c_excel:
        import io # Asegurando que io esté importado para Excel
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_proyectado.to_excel(writer, index=False, sheet_name='Pricing Projections')
        st.download_button(
            label="📊 Exportar Excel",
            data=output.getvalue(),
            file_name=f"PricingMatrix_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.ms-excel",
            width="stretch"
        )