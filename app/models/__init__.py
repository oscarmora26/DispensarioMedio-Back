"""Modelos SQLAlchemy del dominio.

Importar este paquete registra todas las tablas en `Base.metadata`, requisito
de `alembic/env.py`.
"""

from app.models.audit_log import AuditLog
from app.models.brand import Brand
from app.models.doctor import Doctor
from app.models.drug_type import DrugType
from app.models.location import Location
from app.models.medication import Medication
from app.models.patient import Patient
from app.models.role import Role
from app.models.user import User
from app.models.visit import Visit
from app.models.visit_medication import VisitMedication

__all__ = [
    "AuditLog",
    "Brand",
    "Doctor",
    "DrugType",
    "Location",
    "Medication",
    "Patient",
    "Role",
    "User",
    "Visit",
    "VisitMedication",
]
