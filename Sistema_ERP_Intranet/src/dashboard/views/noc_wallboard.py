#!/usr/bin/env python
# coding: utf-8
import streamlit as st
import json
import pandas as pd
from pathlib import Path

# ==========================================
# CONFIGURACIÓN DEL ENTORNO DE KIOSKO (TV 1080p)
# ==========================================
st.markdown("""
    <style>
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}
        [data-testid="collapsedControl"] {display: none;}
        .block-container {padding-top: 0rem; padding-bottom: 1rem; max-width: 95%;}
        
        /* Ajuste de métricas gigantes para TV */
        [data-testid="stMetricValue"] {font-size: 2.8rem !important; font-weight: 700;}
        [data-testid="stMetricLabel"] {font-size: 1.1rem !important; color: #888;}
        
        /* Cajas de métricas para darle aspecto corporativo */
        div[data-testid="metric-container"] {
            background-color: #1E1E1E;
            border: 1px solid #333;
            padding: 5% 5% 5% 10%;
            border-radius: 10px;
            box-shadow: 2px 2px 10px rgba(0,0,0,0.5);
        }
        
        /* Títulos de sección */
        .tv-header {font-size: 1.8rem; color: #4A90E2; font-weight: bold; margin-bottom: 10px;}
        
        /* Forzar ocultamiento de índices en tablas de Streamlit */
        .row_heading.level0 {display:none}
        .blank {display:none}
    </style>
""", unsafe_allow_html=True)

st.html('<meta http-equiv="refresh" content="300">')
STATE_FILE = Path("/home/night/Sistema_ERP_Intranet/output/tv_state.json")

@st.fragment(run_every="10s")
def render_live_wallboard():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        st.warning("⏳ Esperando el primer pulso de telemetría del Backend...")
        return

    # --- ENCABEZADO ---
    col_title, col_time = st.columns([4, 1])
    with col_title:
        st.markdown("<div class='tv-header'>📡 Centro de Operaciones (Vista Ejecutiva)</div>", unsafe_allow_html=True)
    with col_time:
        st.caption(f"⏱️ Actualizado: {data.get('timestamp')}")

    st.divider()

    # ==========================================
    # FILA 1: KPIs FINANCIEROS (DÓLAR)
    # ==========================================
    dolar = data.get("dolar", {})
    k1, k2, k3, k4, k5 = st.columns(5)
    
    k1.metric("1. Tasa Dólar BCV", f"Bs {dolar.get('oficial', 0)}")
    k2.metric("2. Tasa Paralelo", f"Bs {dolar.get('paralelo', 0)}")
    k3.metric("3. Tasa de Cambio (%)", f"{dolar.get('tasa_cambio_pct', 0)} %")
    k4.metric("4. Ratio Actual (%)", f"{dolar.get('ratio_actual', 0)} %")
    
    # Lógica de colores para KPI 5: Verde si es positivo, Rojo (inverso) si es negativo
    diferencia = dolar.get("diferencia", 0.0)
    color_diferencia = "normal" if diferencia >= 0 else "inverse"
    k5.metric("5. Diferencia (+/-)", f"{diferencia} %", delta=f"{diferencia} %", delta_color=color_diferencia)

    st.markdown("<br>", unsafe_allow_html=True)

    # ==========================================
    # FILA 2: SALUD DE SERVIDORES Y JOBS
    # ==========================================
    srv = data.get("servidores", {})
    jobs = data.get("jobs_time", {})
    
    with st.container(border=True):
        st.markdown("**🖥️ Salud del Sistema y Sincronizaciones**")
        # 6 Columnas para incluir Woo y Cashea separados
        s1, s2, s3, s4, s5, s6 = st.columns(6)
        s1.info(f"**Principal (1.184):**\n\n{srv.get('srv_principal')}")
        s2.info(f"**RVTech (1.242):**\n\n{srv.get('srv_rvtech')}")
        s3.success(f"**Motor Rápido:**\n\n{jobs.get('motor_rapido')}")
        s4.success(f"**Motor Nocturno:**\n\n{jobs.get('motor_nocturno')}")
        s5.warning(f"**Últ. Precios Woo:**\n\n{jobs.get('woo_precios')}")
        s6.warning(f"**Últ. Precios Cashea:**\n\n{jobs.get('cashea_precios')}")

    st.markdown("<br>", unsafe_allow_html=True)

    # ==========================================
    # FILA 3: ANÁLISIS COMERCIAL Y STOCK CRÍTICO
    # ==========================================
    col_ventas, col_stock = st.columns([1, 2.5]) # Más espacio al stock
    
    with col_ventas:
        st.markdown("<div class='tv-header'>🔥 Top 5 Más Vendidos (30 Días)</div>", unsafe_allow_html=True)
        top_ventas = data.get("top_ventas", [])
        if top_ventas:
            df_v = pd.DataFrame(top_ventas)
            df_v.rename(columns={'quantity': 'Cant.', 'item_name': 'Producto'}, inplace=True)
            # Solo la tabla, gráfico de torta retirado
            st.dataframe(df_v[['Cant.', 'Producto']], use_container_width=True, hide_index=True)
        else:
            st.info("Recolectando datos de ventas...")

    with col_stock:
        st.markdown("<div class='tv-header'>🚨 Stock Crítico por Marca (Ordenado por Velocidad)</div>", unsafe_allow_html=True)
        criticos = data.get("criticos_marcas", {})
        
        c1, c2, c3, c4, c5 = st.columns(5)
        columnas_marcas = {"UNI-T": c1, "HIKMICRO": c2, "RV": c3, "JCD": c4, "ZIBOO": c5}
        
        for marca, col in columnas_marcas.items():
            with col:
                st.markdown(f"**{marca}**")
                items = criticos.get(marca, [])
                if items:
                    df_m = pd.DataFrame(items)
                    df_m.rename(columns={'sku':'SKU', 'nombre': 'Nombre'}, inplace=True)
                    # Mostramos únicamente SKU y Nombre. Nada de datos sensibles.
                    st.dataframe(df_m[['SKU', 'Nombre']], use_container_width=True, hide_index=True)
                else:
                    st.success("Óptimo")

    # ==========================================
    # FILA 4: TOP VENDEDORES Y SALÓN DE LA FAMA
    # ==========================================
    st.markdown("<br><br>", unsafe_allow_html=True)
    st.markdown("<div class='tv-header'>🏆 Salón de la Fama: Top Vendedores</div>", unsafe_allow_html=True)
    
    vendedores_data = data.get("top_vendedores_reales", {})
    mes_actual = vendedores_data.get("mes_actual", {})
    historial = vendedores_data.get("historial", [])
    
    if not mes_actual:
        st.info("Aún no hay datos suficientes de ventas para premiar.")
    else:
        # Ajustamos las proporciones para darle un poco más de espacio a las fotos dobles
        col_premios, col_foto, col_historial = st.columns([1.1, 1.4, 1.5])
        
        with col_premios:
            st.markdown(f"**🏅 Mes en curso ({mes_actual.get('periodo', '')})**")
            
            st.markdown("#### 🏃 Deja pa' los demás (N° Ventas)")
            for ganador in mes_actual.get('deja_pa_los_demas', []):
                st.success(f"**{ganador['nombre']}** - {ganador['valor']} Ventas")
                
            st.markdown("#### 🦈 Pez Gordo (Volumen $)")
            for ganador in mes_actual.get('pez_gordo', []):
                st.info(f"**{ganador['nombre']}** - ${ganador['valor']:,.2f}")
                
        with col_foto:
            # 🚀 HELPER: Renderizador dinámico de fotos estilo Kiosko
            def renderizar_foto_perfil(id_foto, nombre, subtitulo, mvp=False):
                directorio_fotos = Path("/home/night/Sistema_ERP_Intranet/data/fotos_vendedores")
                posibles_fotos = list(directorio_fotos.glob(f"{id_foto}.*"))
                
                color_sub = "#FFD700" if mvp else "#4A90E2"
                st.markdown(f"<div style='text-align: center; line-height: 1.2; margin-bottom: 8px;'><b>{nombre}</b><br><span style='color: {color_sub}; font-size: 0.9rem;'>{subtitulo}</span></div>", unsafe_allow_html=True)
                
                if posibles_fotos:
                    st.image(str(posibles_fotos[0]), use_container_width=True)
                else:
                    st.markdown(f"""
                        <div style='text-align: center; padding: 20px; background-color: #222; border: 2px dashed #555; border-radius: 10px;'>
                            <h1 style='font-size: 2rem; margin:0;'>📸</h1>
                            <span style='font-size: 0.7rem; color:#888;'>Falta: {id_foto}</span>
                        </div>
                    """, unsafe_allow_html=True)

            nombres_cant = [g['nombre'] for g in mes_actual.get('deja_pa_los_demas', [])]
            nombres_vol = [g['nombre'] for g in mes_actual.get('pez_gordo', [])]
            
            if nombres_cant and nombres_vol:
                mvp_list = list(set(nombres_cant) & set(nombres_vol))
                
                if mvp_list:
                    # 🌟 CASO 1: MVP ABSOLUTO (Un solo líder)
                    ganador_nombre = mvp_list[0]
                    ganador_id_foto = next(g['id_foto'] for g in mes_actual['deja_pa_los_demas'] if g['nombre'] == ganador_nombre)
                    
                    st.markdown(f"<h3 style='text-align: center; color: #FFD700; margin-top:0;'>🌟 MVP DEL MES 🌟</h3>", unsafe_allow_html=True)
                    
                    # Centramos la imagen usando columnas invisibles para que no se vea gigante en la TV
                    _, col_center, _ = st.columns([1, 2, 1])
                    with col_center:
                        renderizar_foto_perfil(ganador_id_foto, ganador_nombre, "Líder Absoluto", mvp=True)
                else:
                    # ⚔️ CASO 2: LÍDERES DIVIDIDOS (Dos fotos lado a lado)
                    st.markdown(f"<h3 style='text-align: center; color: #FFF; margin-top:0;'>⚔️ LÍDERES DEL MES ⚔️</h3>", unsafe_allow_html=True)
                    f1, f2 = st.columns(2)
                    
                    g_cant = mes_actual['deja_pa_los_demas'][0]
                    with f1:
                        renderizar_foto_perfil(g_cant['id_foto'], g_cant['nombre'], "🏃 Más Ventas")
                        
                    g_vol = mes_actual['pez_gordo'][0]
                    with f2:
                        renderizar_foto_perfil(g_vol['id_foto'], g_vol['nombre'], "🦈 Mayor Volumen")
            else:
                st.markdown("<br><h3 style='text-align: center; color: #888;'>Recopilando datos de liderazgo...</h3>", unsafe_allow_html=True)
                
        with col_historial:
            st.markdown("**📜 Historial de Ganadores Anteriores**")
            if historial:
                st.dataframe(pd.DataFrame(historial), use_container_width=True, hide_index=True)
            else:
                st.caption("Aún no hay registros de meses pasados.")