#!/usr/bin/env python
# coding: utf-8

# 🛡️ NUEVO: Importamos UniqueConstraint para la tabla pivote de stock
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, JSON, Text, Boolean, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship, validates  # 🛡️ NUEVO: Importamos el validador estricto
from src.database.db_core import Base

# ==========================================
# MODELOS DE FINANZAS
# ==========================================
class Vendor(Base):
    __tablename__ = "vendors"
    
    id = Column(String, primary_key=True, index=True)
    nombre = Column(String, nullable=False)
    rif = Column(String, unique=True, index=True)
    contacto = Column(String)
    email = Column(String)
    telefono = Column(String)
    terminos = Column(String)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    created_by = Column(String)
    
    transacciones = relationship("LedgerTransaction", back_populates="vendor")
    purchase_orders = relationship("PurchaseOrder", back_populates="vendor")

class LedgerTransaction(Base):
    __tablename__ = "ledger_transactions"
    
    id = Column(String, primary_key=True, index=True)
    fecha = Column(DateTime(timezone=True))
    tipo = Column(String, nullable=False)
    entidad_id = Column(String, ForeignKey("vendors.id"))
    monto = Column(Float, nullable=False, default=0.0)
    vencimiento = Column(DateTime(timezone=True))
    notas = Column(Text)
    status = Column(String)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    created_by = Column(String)
    
    vendor = relationship("Vendor", back_populates="transacciones")

class AuditLog(Base):
    __tablename__ = "audit_logs"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tabla_afectada = Column(String, nullable=False)
    registro_id = Column(String, nullable=False)
    accion = Column(String, nullable=False)
    usuario = Column(String, nullable=False)
    fecha_hora = Column(DateTime(timezone=True), server_default=func.now())
    detalles_json = Column(JSON)

# ==========================================
# MODELOS DE PROYECTOS / KANBAN
# ==========================================

# 🛡️ NUEVA TABLA: Usuarios compartidos (Relación limpia)
class ProjectUserAccess(Base):
    """Tabla pivote para usuarios compartidos en un proyecto."""
    __tablename__ = "project_user_access"
    
    project_id = Column(String, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    # Como tu sistema usa el nombre de usuario directo, lo ponemos como parte de la llave primaria
    username = Column(String, primary_key=True) 

    project = relationship("Project", back_populates="shared_users")

# 🛡️ NUEVA TABLA: Archivos adjuntos (Metadatos seguros)
class ProjectAttachment(Base):
    """Tabla para archivos adjuntos de proyectos."""
    __tablename__ = "project_attachments"
    
    id = Column(String, primary_key=True)
    project_id = Column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    uploaded_at = Column(DateTime(timezone=True), server_default=func.now())

    project = relationship("Project", back_populates="attachments")

class Project(Base):
    """Modelo Principal para Tarjetas Kanban/Proyectos."""
    __tablename__ = "projects"

    id = Column(String, primary_key=True, index=True) 
    titulo = Column(String, nullable=False)
    descripcion = Column(Text)
    owner = Column(String, index=True)
    # ❌ ELIMINADO: shared_with (JSON)
    status = Column(String, index=True)       
    prioridad = Column(String)                
    fecha_inicio = Column(String)             
    fecha_fin = Column(String)
    archivado = Column(Boolean, default=False)
    # ❌ ELIMINADO: archivos (JSON)     

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relaciones existentes
    checklists = relationship("ProjectChecklist", back_populates="project", cascade="all, delete-orphan")
    comentarios = relationship("ProjectComment", back_populates="project", cascade="all, delete-orphan")
    
    # 🛡️ NUEVAS RELACIONES: Reemplazan a las columnas JSON
    shared_users = relationship("ProjectUserAccess", back_populates="project", cascade="all, delete-orphan")
    attachments = relationship("ProjectAttachment", back_populates="project", cascade="all, delete-orphan")

class ProjectChecklist(Base):
    """Sub-tareas o Checklist asociado a un Proyecto."""
    __tablename__ = "project_checklists"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    texto = Column(String, nullable=False)
    done = Column(Boolean, default=False)

    project = relationship("Project", back_populates="checklists")

class ProjectComment(Base):
    """Historial de Comentarios por Proyecto."""
    __tablename__ = "project_comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    usuario = Column(String, nullable=False)
    texto = Column(Text, nullable=False)
    fecha_hora = Column(String) 

    project = relationship("Project", back_populates="comentarios")

# ==========================================
# MODELOS DE INVENTARIO Y CATÁLOGO
# ==========================================
class Product(Base):
    """Catálogo maestro de productos."""
    __tablename__ = "products"

    id = Column(String, primary_key=True, index=True)
    sku = Column(String, unique=True, index=True, nullable=False)
    nombre = Column(String, nullable=False)
    costo_base = Column(Float, nullable=False, default=0.0)   # 🛡️ Obligatorio
    precio_venta = Column(Float, nullable=False, default=0.0) # 🛡️ Obligatorio

    # 🛡️ FIREWALL DE DATOS: Bloquea precios o costos negativos antes de tocar SQLite
    @validates('costo_base', 'precio_venta')
    def validate_finanzas_positivas(self, key, value):
        if value is not None and float(value) < 0:
            raise ValueError(f"Error de Integridad: '{key}' no puede ser negativo. Se recibió: {value}")
        return value

    marca = Column(String, default="General")
    descripcion_breve = Column(Text)
    imagen_url = Column(String)
    
    # ❌ ELIMINADO: stock_galerias y stock_millennium (Reemplazados por ProductStockLocation)
    
    status_prediccion = Column(String, default="NUEVO")
    
    # --- NUEVO: Para Órdenes de Compra ---
    inventario_entrante = Column(Float, default=0.0)

    # 🛡️ PILAR 3: OPTIMISTIC LOCKING (Control de Concurrencia)
    # Evita que dos usuarios modifiquen el Costo MAC al mismo tiempo sin saberlo
    version_id = Column(Integer, nullable=False, default=1)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # 🛡️ Le decimos a SQLAlchemy qué columna usar para controlar las versiones
    __mapper_args__ = {
        "version_id_col": version_id
    }

    pricing_matrix = relationship("PricingMatrix", back_populates="product", uselist=False, cascade="all, delete-orphan")
    purchase_items = relationship("PurchaseOrderItem", back_populates="product")
    
    # 🛡️ PILAR 1: Relación con la tabla multi-tienda
    stocks = relationship("ProductStockLocation", back_populates="product", cascade="all, delete-orphan")

class PricingMatrix(Base):
    """Matriz de Costeo y Precios de Borrador."""
    __tablename__ = "pricing_matrix"

    id = Column(String, primary_key=True)
    product_id = Column(String, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, unique=True)

    costo_fabrica = Column(Float, default=0.0)
    peso_fabrica = Column(Float, default=0.0)
    costo_ada = Column(Float, default=0.0)
    peso_ada = Column(Float, default=0.0)
    costo_estella = Column(Float, default=0.0)
    peso_estella = Column(Float, default=0.0)
    volumen_m3 = Column(Float, default=0.0)
    envio_porcentual = Column(Float, default=0.0)
    marketing_porcentual = Column(Float, default=0.0)
    margen_deseado = Column(Float, default=0.30)
    precio_competencia = Column(Float, default=0.0)

    product = relationship("Product", back_populates="pricing_matrix")

class InventoryLocation(Base):
    __tablename__ = "inventory_locations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nombre = Column(String, nullable=False)
    tipo = Column(String, nullable=False) 
    loyverse_store_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

# 🛡️ NUEVA TABLA (PILAR 1): Vista Materializada del Stock Multi-Tienda
class ProductStockLocation(Base):
    """Tabla puente: Guarda la foto actual del stock por SKU y Sucursal"""
    __tablename__ = "product_stock_locations"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    # ⚠️ Nota: product_id es String porque Product.id es String en tu sistema
    product_id = Column(String, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    location_id = Column(Integer, ForeignKey("inventory_locations.id", ondelete="CASCADE"), nullable=False)

    # El stock actual materializado para lecturas super rápidas
    stock_actual = Column(Float, nullable=False, default=0.0)

    # 🛡️ Integridad: Evita que el mismo producto tenga dos líneas de stock en la misma tienda
    __table_args__ = (UniqueConstraint('product_id', 'location_id', name='_product_location_uc'),)

    location = relationship("InventoryLocation")
    product = relationship("Product", back_populates="stocks")

class CommercialAlly(Base):
    __tablename__ = "commercial_allies"

    id = Column(String, primary_key=True, index=True)
    nombre = Column(String, nullable=False)
    tipo_alianza = Column(String) 
    dias_credito = Column(Integer, default=0)
    moneda_pago = Column(String, default="USD")
    descuento_fabrica = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class InventoryMovement(Base):
    __tablename__ = "inventory_movements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(String, ForeignKey("products.id"), nullable=False)
    source_location_id = Column(Integer, ForeignKey("inventory_locations.id"), nullable=True) 
    dest_location_id = Column(Integer, ForeignKey("inventory_locations.id"), nullable=True)   
    cantidad = Column(Integer, nullable=False)
    fecha = Column(DateTime(timezone=True), server_default=func.now())
    tipo = Column(String, nullable=False) 
    status = Column(String, nullable=False) 
    created_by = Column(String) 

    product = relationship("Product")
    source_location = relationship("InventoryLocation", foreign_keys=[source_location_id])
    dest_location = relationship("InventoryLocation", foreign_keys=[dest_location_id])


class PurchaseOrder(Base):
    """Orden de Compra Cabecera."""
    __tablename__ = "purchase_orders"

    id = Column(String, primary_key=True, index=True)
    vendor_id = Column(String, ForeignKey("vendors.id"))
    status = Column(String, default="Borrador") # Opciones: "Borrador", "Esperado", "Recibido"

    fecha_creacion = Column(DateTime(timezone=True), server_default=func.now())
    fecha_esperada = Column(DateTime(timezone=True))
    notas = Column(Text)

    costos_adicionales_envio = Column(Float, default=0.0)
    total_orden = Column(Float, default=0.0)

    # Relaciones
    vendor = relationship("Vendor", back_populates="purchase_orders")
    items = relationship("PurchaseOrderItem", back_populates="purchase_order", cascade="all, delete-orphan")

class PurchaseOrderItem(Base):
    """Líneas de detalle de la Orden de Compra."""
    __tablename__ = "purchase_order_items"

    id = Column(String, primary_key=True, index=True)
    po_id = Column(String, ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False) # 🛡️ Huérfanos prohibidos
    product_id = Column(String, ForeignKey("products.id"), nullable=False)                       # 🛡️ Huérfanos prohibidos

    cantidad = Column(Float, nullable=False, default=0.0)
    cantidad_recibida = Column(Float, nullable=False, default=0.0)
    costo_unitario = Column(Float, nullable=False, default=0.0)
    total_linea = Column(Float, nullable=False, default=0.0)

    # 🛡️ FIREWALL DE DATOS: Previene inventario fantasma o dinero negativo
    @validates('cantidad', 'cantidad_recibida', 'costo_unitario')
    def validate_cantidades_logicas(self, key, value):
        if value is not None and float(value) < 0:
            raise ValueError(f"Error Crítico en OC: '{key}' no puede ser negativo. Se recibió: {value}")
        return value

    # Relaciones
    purchase_order = relationship("PurchaseOrder", back_populates="items")
    product = relationship("Product", back_populates="purchase_items")




# ==========================================
# PATRÓN OUTBOX: Cola de tareas para APIs
# ==========================================
class ApiOutbox(Base):
    """Cola de tareas transaccional para evitar Dual-Write con APIs externas (Loyverse)."""
    __tablename__ = "api_outbox"

    id = Column(Integer, primary_key=True, autoincrement=True)
    target_system = Column(String, default="LOYVERSE") 
    endpoint = Column(String, nullable=False) # Ej. 'adjust_inventory'
    payload_json = Column(Text, nullable=False) # El body del request HTTP
    status = Column(String, default="PENDING", index=True) # PENDING, PROCESSED, FAILED
    retry_count = Column(Integer, default=0)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())






    
# ==========================================
# UTILIDADES FINANCIERAS
# ==========================================
def calcular_nuevo_costo_promedio(
    stock_actual: float,
    costo_actual: float,
    cantidad_recibida: float,
    costo_nuevo: float,
    costos_adicionales_prorrateados: float = 0.0
) -> float:
    """Calcula el MAC con protección de ceros y precisión financiera."""
    # 🛡️ FAIL-SAFE 1: Si el stock actual es <= 0, el MAC se reinicia al costo de la nueva compra
    if stock_actual <= 0:
        return round(costo_nuevo + costos_adicionales_prorrateados, 4)

    stock_total = stock_actual + cantidad_recibida

    # 🛡️ FAIL-SAFE 2: Prevenir división por cero si la entrada neta es 0
    if stock_total <= 0:
        return round(costo_actual, 4)

    mac = ((stock_actual * costo_actual) + (cantidad_recibida * (costo_nuevo + costos_adicionales_prorrateados))) / stock_total

    # 🛡️ ROUNDING: Evita la inflación por basura flotante (Floating Point Drift)
    return round(mac, 4)

