#!/usr/bin/env python
# coding: utf-8

# Calcula y muestra los KPIs principales en la parte superior.

import streamlit as st
import pandas as pd
from sqlalchemy import func
from src.database.db_core import SessionLocal
from src.database.models import PurchaseOrder, PurchaseOrderItem

def render_kpi_row(df: pd.DataFrame):
    """
    Calcula y renderiza la fila de tarjetas (KPIs) gerenciales.
    """
    # 1. Valores por defecto (Fail-safe)
    criticos_count, items_to_reorder, total_qty_needed = 0, 0, 0

    # 2. Validación de datos sin interrumpir el renderizado
    if df.empty:
        st.warning("⚠️ No hay datos en el DataFrame. Genera el reporte desde el backend.")
    else:
        if 'status' in df.columns: 
            criticos_count = df[df['status'] == 'CRITICO'].shape[0]
        if 'qty_to_reorder' in df.columns:
            items_to_reorder = df[df['qty_to_reorder'] > 0].shape[0]
            total_qty_needed = int(df['qty_to_reorder'].sum())

    # Definir columnas layout (4 métricas)
    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    
    # 3. Tarjeta KPI 1: Items Críticos
    with kpi1:
        with st.container(border=True):
            st.metric(label="🚨 Items Críticos", value=criticos_count, delta="Acción Inmediata", delta_color="inverse")

    # 4. Tarjeta KPI 2: Items a Pedir
    with kpi2:
        with st.container(border=True):
            st.metric(
                label="📦 SKUs a Pedir",
                value=items_to_reorder,
                help="Productos donde el Stock Total < Punto de Reorden"
            )

    # 5. Tarjeta KPI 3: Cantidad Total a Comprar
    with kpi3:
        with st.container(border=True):
            st.metric(
                label="🛒 Unidades Totales",
                value=f"{total_qty_needed:,}".replace(",", "."),
                help="Suma total de cantidad a reordenar"
            )

    # 6. Mercancía en Tránsito (Cálculo en vivo desde la BD)
    db = SessionLocal()
    try:
        # Sumamos (pedida - recibida) solo de órdenes activas
        transito_query = db.query(
            func.sum(PurchaseOrderItem.cantidad - PurchaseOrderItem.cantidad_recibida)
        ).join(
            PurchaseOrder, PurchaseOrderItem.po_id == PurchaseOrder.id
        ).filter(
            PurchaseOrder.status.in_(['Esperado', 'Pagado', 'Recibido Parcialmente'])
        ).scalar()
        
        total_transit = int(transito_query) if transito_query is not None else 0
    except Exception as e:
        total_transit = 0
    finally:
        db.close() # 🛡️ Innegociable para evitar Database Locked

    # 7. Tarjeta KPI 4: Tránsito
    with kpi4:
        with st.container(border=True):
            st.metric(
                label="🚚 En Tránsito",
                value=f"{total_transit:,} unds".replace(",", "."),
                help="Mercancía comprada pendiente de llegada (Live SQL)"
            )
    
    st.markdown("---")