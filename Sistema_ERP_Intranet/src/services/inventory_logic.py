#!/usr/bin/env python
# coding: utf-8

from sqlalchemy.orm import Session
from src.database.models import InventoryMovement, ProductStockLocation

def registrar_movimiento_kardex(db_session: Session, product_id: str, location_id: int,
                                delta_qty: float, m_type: str, user: str):
    """
    Event Sourcing: Toda alteración de stock DEBE pasar por esta función.
    Garantiza la inmutabilidad (Kardex) y actualiza la vista rápida (ProductStockLocation)
    usando bloqueos a nivel de base de datos para evitar Race Conditions.
    """
    if delta_qty == 0:
        return # No hay movimiento matemático

    # 1. Insertar el evento inmutable (La Verdad Histórica)
    movimiento = InventoryMovement(
        product_id=product_id,
        dest_location_id=location_id if delta_qty > 0 else None,
        source_location_id=location_id if delta_qty < 0 else None,
        cantidad=abs(delta_qty),
        tipo=m_type,
        status="Completado",
        created_by=user
    )
    db_session.add(movimiento)

    # 2. Actualizar la Vista Materializada Multi-Tienda (ProductStockLocation)
    # 🛡️ with_for_update() bloquea la fila en SQLite/Postgres hasta que termine la transacción,
    # impidiendo que dos usuarios descuadren el stock si venden al mismo tiempo.
    stock_loc = db_session.query(ProductStockLocation).filter_by(
        product_id=product_id, location_id=location_id
    ).with_for_update().first()

    if not stock_loc:
        stock_loc = ProductStockLocation(product_id=product_id, location_id=location_id, stock_actual=0.0)
        db_session.add(stock_loc)

    stock_loc.stock_actual += delta_qty

    # 3. Validación de Invariante de Negocio
    if stock_loc.stock_actual < 0:
        raise ValueError(f"Stock negativo detectado para el producto {product_id} en locación {location_id}. Operación abortada.")

    db_session.flush() # Sincroniza con la base de datos sin cerrar la transacción