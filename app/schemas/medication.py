"""Schemas del contrato de medicamentos (FEAT-010 D-4, D-5, D-6)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator
from pydantic_core import PydanticCustomError

# Catalogo cerrado de ordenacion del listado (FEAT-010 D-3).
SORT_FIELDS = (
    "description",
    "dose",
    "drug_type_id",
    "brand_id",
    "location_id",
    "created_at",
    "is_active",
    "id",
)
SORT_PATTERN = r"^-?(?:description|dose|drug_type_id|brand_id|location_id|created_at|is_active|id)$"

_TEXT_FIELDS = ("description", "dose")

_EMPTY_MESSAGES = {
    "description": "La descripción no puede estar vacía",
    "dose": "La dosis no puede estar vacía",
}


class _MedicationBase(BaseModel):
    """Campos de negocio compartidos por alta y reemplazo completo."""

    description: str = Field(
        max_length=100,
        description="Descripción del medicamento, entre 1 y 100 caracteres",
    )
    drug_type_id: int = Field(
        ge=1,
        description="Identificador del tipo de fármaco",
    )
    brand_id: int = Field(
        ge=1,
        description="Identificador de la marca",
    )
    location_id: int = Field(
        ge=1,
        description="Identificador de la ubicación",
    )
    dose: str = Field(
        max_length=100,
        description="Dosis del medicamento, entre 1 y 100 caracteres",
    )

    @field_validator("description", "dose", mode="before")
    @classmethod
    def _strip_text_fields(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("description", "dose")
    @classmethod
    def _validate_text_fields(cls, value: str, info: ValidationInfo) -> str:
        if not value:
            field_name = info.field_name or "description"
            raise PydanticCustomError(
                f"{field_name}_empty",
                _EMPTY_MESSAGES.get(field_name, "El campo no puede estar vacío"),
            )
        return value


class MedicationCreate(_MedicationBase):
    """Alta de medicamento; `is_active` es opcional (D-5, por defecto `true`)."""

    is_active: bool = Field(default=True, description="Estado inicial del medicamento")


class MedicationUpdate(_MedicationBase):
    """Reemplazo completo de un medicamento (D-1); los seis campos obligatorios."""

    is_active: bool = Field(description="Estado resultante del medicamento")


class MedicationRead(BaseModel):
    """Forma unica de un medicamento (D-4): siete claves planas sin anidados."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    description: str
    drug_type_id: int
    brand_id: int
    location_id: int
    dose: str
    is_active: bool


class MedicationListResponse(BaseModel):
    """Pagina de medicamentos."""

    items: list[MedicationRead]
    total: int
    page: int
    size: int
    pages: int
