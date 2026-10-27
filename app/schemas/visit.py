"""Schemas for the visit management contract (FEAT-013 D-4, D-6, D-7, D-8)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_serializer
from pydantic_core import PydanticCustomError

# Closed status catalog of the `visits` table (`ck_visits_status_allowed`).
VISIT_STATUS_VALUES = ("REGISTRADA", "ANULADA")
VISIT_STATUS_PATTERN = r"^(?:REGISTRADA|ANULADA)$"

# Closed sort catalog of the visit listing (D-6). Default is `-visited_at`.
SORT_FIELDS = ("visited_at", "created_at", "id", "status")
SORT_PATTERN = r"^-?(?:visited_at|created_at|id|status)$"
DEFAULT_SORT = "-visited_at"

# Field-level messages of rules 6-14 and 33 (travel in `errors[0]`, no `body` prefix).
DOCTOR_FROM_TOKEN_MESSAGE = "El médico se obtiene del token"
DOCTOR_REQUIRED_MESSAGE = "El médico es obligatorio"
DOCTOR_INACTIVE_MESSAGE = "El médico debe estar activo"
DOCTOR_FILTER_FORBIDDEN_MESSAGE = "Solo el administrador puede filtrar por médico"
PATIENT_REQUIRED_MESSAGE = "El paciente es obligatorio"
PATIENT_INACTIVE_MESSAGE = "El paciente debe estar activo"
VISITED_AT_REQUIRED_MESSAGE = "La fecha de la visita es obligatoria"
VISITED_AT_FUTURE_MESSAGE = "La fecha de la visita no puede ser futura"
SYMPTOMS_RANGE_MESSAGE = "Los síntomas deben tener entre 3 y 2000 caracteres"
SYMPTOMS_MAX_MESSAGE = "Los síntomas no pueden superar 2000 caracteres"
RECOMMENDATIONS_MAX_MESSAGE = "Las recomendaciones no pueden superar 2000 caracteres"
MEDICATION_INACTIVE_MESSAGE = "El medicamento debe estar activo"
DUPLICATE_MEDICATION_MESSAGE = "Medicamento duplicado en la visita"
QUANTITY_POSITIVE_MESSAGE = "La cantidad debe ser mayor que cero"
INSTRUCTIONS_MAX_MESSAGE = "Las instrucciones no pueden superar 255 caracteres"
REASON_REQUIRED_MESSAGE = "El motivo es obligatorio"
REASON_MAX_MESSAGE = "El motivo no puede superar 500 caracteres"
DATE_RANGE_MESSAGE = "La fecha inicial no puede ser mayor que la final"

SYMPTOMS_MIN_LENGTH = 3
SYMPTOMS_MAX_LENGTH = 2000
RECOMMENDATIONS_MAX_LENGTH = 2000
INSTRUCTIONS_MAX_LENGTH = 255
REASON_MAX_LENGTH = 500

#: Keys added only for annulled visits (D-7).
ANNULLED_KEYS = ("annulled_at", "annulled_by", "annulment_reason")


class VisitMedicationInput(BaseModel):
    """A medication delivered during the visit (rule 12)."""

    medication_id: int = Field(description="Identificador del medicamento")
    quantity: Decimal | None = Field(
        default=None,
        description="Cantidad entregada; opcional y mayor que cero",
    )
    instructions: str | None = Field(
        default=None,
        description="Indicaciones de uso; opcional, maximo 255 caracteres",
    )

    @field_validator("instructions", mode="before")
    @classmethod
    def _normalize_instructions(cls, value: object) -> object:
        """Trim `instructions` and map blank text to `null` (rule 11 spirit)."""
        if not isinstance(value, str):
            return value
        trimmed = value.strip()
        return trimmed or None


class _VisitFields(BaseModel):
    """Fields shared by creation and full replacement (rule 15, D-10).

    Presence is validated by the service so the rejection carries the exact
    Spanish message and field of rules 6-11 (`doctor_id`, `patient_id`,
    `visited_at`, `symptoms`).
    """

    doctor_id: int | None = Field(
        default=None,
        description="Medico de la visita; siempre se toma del token y no se puede elegir",
    )
    patient_id: int | None = Field(
        default=None,
        gt=0,
        description="Paciente atendido; debe existir y estar activo",
    )
    visited_at: datetime | None = Field(
        default=None,
        description="Fecha y hora de la visita; obligatoria y no futura",
    )
    symptoms: str | None = Field(
        default=None,
        description="Sintomas, entre 3 y 2000 caracteres",
    )
    recommendations: str | None = Field(
        default=None,
        description="Recomendaciones; opcional, maximo 2000 caracteres",
    )
    medications: list[VisitMedicationInput] | None = Field(
        default=None,
        description="Medicamentos entregados; opcional y sin duplicados",
    )

    @field_validator("symptoms", mode="before")
    @classmethod
    def _normalize_symptoms(cls, value: object) -> object:
        """Trim `symptoms` and enforce the 3..2000 character window (rule 10)."""
        if not isinstance(value, str):
            return value
        trimmed = value.strip()
        if len(trimmed) < SYMPTOMS_MIN_LENGTH:
            raise PydanticCustomError("symptoms_range", SYMPTOMS_RANGE_MESSAGE)
        if len(trimmed) > SYMPTOMS_MAX_LENGTH:
            raise PydanticCustomError("symptoms_max", SYMPTOMS_MAX_MESSAGE)
        return trimmed

    @field_validator("recommendations", mode="before")
    @classmethod
    def _normalize_recommendations(cls, value: object) -> object:
        """Trim `recommendations`; blank text becomes `null` (rule 11)."""
        if not isinstance(value, str):
            return value
        trimmed = value.strip()
        if not trimmed:
            return None
        if len(trimmed) > RECOMMENDATIONS_MAX_LENGTH:
            raise PydanticCustomError("recommendations_max", RECOMMENDATIONS_MAX_MESSAGE)
        return trimmed


class VisitCreate(_VisitFields):
    """Visit registration body (rule 15)."""


class VisitUpdate(_VisitFields):
    """Full replacement of a visit (rule 29, D-10)."""


class VisitAnnulRequest(BaseModel):
    """Annulment body (rule 33)."""

    reason: str = Field(description="Motivo de la anulacion; obligatorio, maximo 500 caracteres")

    @field_validator("reason", mode="before")
    @classmethod
    def _normalize_reason(cls, value: object) -> object:
        """Trim `reason` and validate presence and length (rule 33)."""
        if not isinstance(value, str):
            return value
        trimmed = value.strip()
        if not trimmed:
            raise PydanticCustomError("reason_required", REASON_REQUIRED_MESSAGE)
        if len(trimmed) > REASON_MAX_LENGTH:
            raise PydanticCustomError("reason_max", REASON_MAX_MESSAGE)
        return trimmed


class VisitDoctorRef(BaseModel):
    """Doctor reference nested in a visit (D-7, D-8)."""

    id: int
    name: str


class VisitPatientRef(BaseModel):
    """Patient reference nested in a visit (D-7, D-8)."""

    id: int
    name: str
    cedula: str


class VisitMedicationRead(BaseModel):
    """Medication detail of a visit with its catalog description and dose (D-7)."""

    medication_id: int
    description: str
    dose: str
    quantity: Decimal | None
    instructions: str | None


class VisitRead(BaseModel):
    """Detail shape of a visit (D-7).

    Annulment fields only travel on annulled visits: the model serializer drops
    them when `annulled_at` is absent, while `recommendations` keeps its `null`
    value (rule 16, CA-14, CA-15).
    """

    id: int
    doctor: VisitDoctorRef
    patient: VisitPatientRef
    visited_at: datetime
    symptoms: str
    recommendations: str | None
    status: str
    created_by: int
    created_at: datetime
    updated_at: datetime
    medications: list[VisitMedicationRead]
    annulled_at: datetime | None = None
    annulled_by: int | None = None
    annulment_reason: str | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_annulment(
        self,
        handler: Callable[[Any], dict[str, Any]],
    ) -> dict[str, Any]:
        """Remove the annulment keys when the visit is not annulled (D-7)."""
        data = handler(self)
        if data.get("annulled_at") is None:
            for key in ANNULLED_KEYS:
                data.pop(key, None)
        return data


class VisitListItem(BaseModel):
    """Summary shape used by the paginated listing (D-8)."""

    id: int
    doctor: VisitDoctorRef
    patient: VisitPatientRef
    visited_at: datetime
    symptoms: str
    status: str
    created_by: int


class VisitListResponse(BaseModel):
    """Page of visits."""

    items: list[VisitListItem]
    total: int
    page: int
    size: int
    pages: int


class VisitAnnulResponse(BaseModel):
    """Literal annulment shape of the API document (rule 36)."""

    id: int
    status: str
    annulled_at: datetime
    annulled_by: int
    annulment_reason: str
