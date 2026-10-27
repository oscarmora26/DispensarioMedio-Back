"""Utilidades de apoyo para los tests de visitas (FEAT-013, autorizacion FEAT-019)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import auth_headers, cached_password_hash, create_auth_user, token_for
from tests.db_support import (
    create_doctor,
    create_medication,
    create_patient,
    fetch_scalar,
    unique_cedula,
    unique_suffix,
)
from tests.users_support import assert_error_envelope, error_fields

VISITS_PATH = "/api/v1/visits"

#: Claves exactas del detalle de una visita registrada (FEAT-013 D-7).
DETAIL_KEYS = {
    "id",
    "doctor",
    "patient",
    "visited_at",
    "symptoms",
    "recommendations",
    "status",
    "created_by",
    "created_at",
    "updated_at",
    "medications",
}

#: Claves que solo aparecen en el detalle de una visita anulada (D-7, CA-23).
ANNULLED_KEYS = {"annulled_at", "annulled_by", "annulment_reason"}

#: Claves exactas del item de listado (FEAT-013 D-8, CA-22).
LIST_ITEM_KEYS = {
    "id",
    "doctor",
    "patient",
    "visited_at",
    "symptoms",
    "status",
    "created_by",
}

#: Claves exactas del detalle de medicamento (D-7, CA-13).
MEDICATION_KEYS = {"medication_id", "description", "dose", "quantity", "instructions"}

#: Claves exactas de la respuesta de anulacion (regla 36, CA-29).
ANNUL_RESPONSE_KEYS = {"id", "status", "annulled_at", "annulled_by", "annulment_reason"}

#: Claves de la referencia anidada del medico (D-7, D-8).
DOCTOR_REF_KEYS = {"id", "name"}

#: Claves de la referencia anidada del paciente (D-7, D-8).
PATIENT_REF_KEYS = {"id", "name", "cedula"}

__all__ = [
    "ANNULLED_KEYS",
    "ANNUL_RESPONSE_KEYS",
    "DETAIL_KEYS",
    "DOCTOR_REF_KEYS",
    "LIST_ITEM_KEYS",
    "MEDICATION_KEYS",
    "PATIENT_REF_KEYS",
    "VISITS_PATH",
    "add_visit_medication",
    "admin_actor",
    "annul_path",
    "annul_visit_row",
    "assert_error_envelope",
    "assert_field_error",
    "count_visits",
    "create_medication_row",
    "doctor_row",
    "error_fields",
    "fetch_visit_medication_rows",
    "fetch_visit_row",
    "medico_actor",
    "nonexistent_visit_id",
    "patient_row",
    "register_visit",
    "unique_cedula",
    "unique_suffix",
    "unlinked_medico_actor",
    "visit_body",
    "visit_owner_actor",
    "visit_path",
]


def visit_path(visit_id: int | str) -> str:
    """Ruta de detalle/actualizacion de una visita."""
    return f"{VISITS_PATH}/{visit_id}"


def annul_path(visit_id: int | str) -> str:
    """Ruta de anulacion de una visita."""
    return f"{VISITS_PATH}/{visit_id}/annul"


def assert_field_error(
    response: Any,
    status: int,
    code: str,
    field: str,
    message: str | None = None,
) -> dict[str, Any]:
    """Comprueba el envelope y el primer error de campo de una respuesta.

    El detalle de las reglas 6-14 viaja en `errors[0]` con el `field` indicado
    y sin prefijo `body`, asi que el assert es sobre la primera entrada.
    """
    body = assert_error_envelope(response, status, code)
    assert body["errors"], body
    first = body["errors"][0]
    assert first["field"] == field, body
    if message is not None:
        assert first["message"] == message, body
    return body


async def admin_actor(conn: AsyncConnection) -> tuple[int, dict[str, str]]:
    """Crea un ADMIN activo y devuelve su id y sus cabeceras Bearer."""
    suffix = unique_suffix()
    user_id = await create_auth_user(
        conn,
        username=f"vactor-{suffix}",
        email=f"vactor.{suffix}@example.test",
        role="ADMIN",
        password_hash=cached_password_hash(),
    )
    return user_id, auth_headers(token_for(user_id))


async def medico_actor(
    conn: AsyncConnection,
    *,
    doctor_id: int | None = None,
    is_active: bool = True,
) -> tuple[int, dict[str, str]]:
    """Crea un MEDICO (asociado a `doctor_id` si se indica) con sus cabeceras."""
    suffix = unique_suffix()
    user_id = await create_auth_user(
        conn,
        username=f"vmedico_{suffix}",
        email=f"vmedico_{suffix}@example.test",
        role="MEDICO",
        password_hash=cached_password_hash(),
        doctor_id=doctor_id,
        is_active=is_active,
    )
    return user_id, auth_headers(token_for(user_id, role="MEDICO", doctor_id=doctor_id))


async def doctor_row(
    conn: AsyncConnection,
    *,
    name: str | None = None,
    is_active: bool = True,
) -> int:
    """Inserta un medico y devuelve su id (inactivo si se pide)."""
    doctor_id = await create_doctor(conn, name=name or f"Dr. Visita {unique_suffix()}")
    if not is_active:
        await conn.execute(
            text("UPDATE doctors SET is_active = false WHERE id = :doctor_id"),
            {"doctor_id": doctor_id},
        )
    return doctor_id


async def patient_row(
    conn: AsyncConnection,
    *,
    name: str | None = None,
    is_active: bool = True,
) -> int:
    """Inserta un paciente y devuelve su id (inactivo si se pide)."""
    patient_id = await create_patient(conn, name=name or f"Paciente {unique_suffix()}")
    if not is_active:
        await conn.execute(
            text("UPDATE patients SET is_active = false WHERE id = :patient_id"),
            {"patient_id": patient_id},
        )
    return patient_id


async def visit_owner_actor(
    conn: AsyncConnection,
    *,
    doctor_is_active: bool = True,
    is_active: bool = True,
) -> tuple[int, int, dict[str, str]]:
    """Crea un MEDICO dueño de sus visitas y devuelve `(user_id, doctor_id, headers)`.

    El medico de la visita sale del usuario (FEAT-019), asi que la visita de este
    actor le pertenece. Llamarlo dos veces produce dos medicos con sendos
    usuarios, util para el caso de "visita de otro medico".
    """
    doctor_id = await doctor_row(conn, is_active=doctor_is_active)
    user_id, headers = await medico_actor(conn, doctor_id=doctor_id, is_active=is_active)
    return user_id, doctor_id, headers


async def unlinked_medico_actor(
    conn: AsyncConnection, *, is_active: bool = True
) -> tuple[int, dict[str, str]]:
    """Crea un MEDICO sin `doctor_id` asociado y devuelve su id y cabeceras.

    Ese usuario no puede registrar visitas: `POST` responde `403`
    `doctor_not_associated` (FEAT-019 CA-6).
    """
    return await medico_actor(conn, is_active=is_active)


async def create_medication_row(
    conn: AsyncConnection,
    *,
    description: str | None = None,
    dose: str = "500 mg",
    is_active: bool = True,
) -> int:
    """Inserta un medicamento y devuelve su id (inactivo si se pide)."""
    medication_id = await create_medication(conn, description=description, dose=dose)
    if not is_active:
        await conn.execute(
            text("UPDATE medications SET is_active = false WHERE id = :medication_id"),
            {"medication_id": medication_id},
        )
    return medication_id


def past_visited_at(minutes: int = 5) -> str:
    """Instante ISO en UTC, anterior a `now()`, valido para registrar visitas."""
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


def visit_body(
    *,
    doctor_id: int | None,
    patient_id: int | None,
    visited_at: str | None = None,
    symptoms: str = "Dolor de cabeza y fiebre",
    recommendations: str | None = "Reposo e hidratación",
    medications: Sequence[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Body del §11 con los valores indicados; `None` omite la clave."""
    body: dict[str, Any] = {
        "doctor_id": doctor_id,
        "patient_id": patient_id,
        "visited_at": visited_at or past_visited_at(),
        "symptoms": symptoms,
        "recommendations": recommendations,
        "medications": list(medications) if medications is not None else [],
    }
    body.update(extra)
    return {
        key: value for key, value in body.items() if value is not None or key == "recommendations"
    }


async def register_visit(
    conn: AsyncConnection,
    *,
    doctor_id: int,
    patient_id: int,
    created_by: int,
    visited_at: datetime | None = None,
    symptoms: str = "Síntomas de prueba",
    recommendations: str | None = None,
    status: str = "REGISTRADA",
    medications: Sequence[tuple[int, Any, str | None]] = (),
) -> int:
    """Inserta una visita por SQL (con sus detalles) y devuelve su id."""
    result = await conn.execute(
        text(
            """
            INSERT INTO visits (
                doctor_id, patient_id, visited_at, symptoms, recommendations,
                status, created_by
            )
            VALUES (
                :doctor_id, :patient_id, :visited_at, :symptoms, :recommendations,
                :status, :created_by
            )
            RETURNING id
            """
        ),
        {
            "doctor_id": doctor_id,
            "patient_id": patient_id,
            "visited_at": visited_at or datetime.now(UTC) - timedelta(minutes=5),
            "symptoms": symptoms,
            "recommendations": recommendations,
            "status": status,
            "created_by": created_by,
        },
    )
    visit_id = int(result.scalar_one())
    for medication_id, quantity, instructions in medications:
        await add_visit_medication(
            conn,
            visit_id=visit_id,
            medication_id=medication_id,
            quantity=quantity,
            instructions=instructions,
        )
    return visit_id


async def add_visit_medication(
    conn: AsyncConnection,
    *,
    visit_id: int,
    medication_id: int,
    quantity: Any = None,
    instructions: str | None = None,
) -> int:
    """Inserta un detalle de medicamento de una visita y devuelve su id."""
    result = await conn.execute(
        text(
            """
            INSERT INTO visit_medications (visit_id, medication_id, quantity, instructions)
            VALUES (:visit_id, :medication_id, :quantity, :instructions)
            RETURNING id
            """
        ),
        {
            "visit_id": visit_id,
            "medication_id": medication_id,
            "quantity": quantity,
            "instructions": instructions,
        },
    )
    return int(result.scalar_one())


async def annul_visit_row(
    conn: AsyncConnection,
    visit_id: int,
    *,
    annulled_by: int,
    reason: str = "Visita registrada por error",
) -> None:
    """Anula una visita por SQL para preparar escenarios de listado y detalle."""
    await conn.execute(
        text(
            """
            UPDATE visits
            SET status = 'ANULADA', annulled_by = :annulled_by,
                annulled_at = :annulled_at, annulment_reason = :reason
            WHERE id = :visit_id
            """
        ),
        {
            "visit_id": visit_id,
            "annulled_by": annulled_by,
            "annulled_at": datetime.now(UTC),
            "reason": reason,
        },
    )


async def fetch_visit_row(conn: AsyncConnection, visit_id: int) -> dict[str, Any] | None:
    """Lee la fila completa de una visita, incluidas las fechas y la anulacion."""
    result = await conn.execute(
        text(
            """
            SELECT id, doctor_id, patient_id, visited_at, symptoms, recommendations,
                   status, created_by, annulled_by, annulled_at, annulment_reason,
                   created_at, updated_at
            FROM visits
            WHERE id = :visit_id
            """
        ),
        {"visit_id": visit_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row is not None else None


async def fetch_visit_medication_rows(conn: AsyncConnection, visit_id: int) -> list[dict[str, Any]]:
    """Lee los detalles de medicamento de una visita ordenados por medicamento."""
    result = await conn.execute(
        text(
            """
            SELECT medication_id, quantity, instructions
            FROM visit_medications
            WHERE visit_id = :visit_id
            ORDER BY medication_id
            """
        ),
        {"visit_id": visit_id},
    )
    return [dict(row) for row in result.mappings().all()]


async def count_visits(conn: AsyncConnection) -> int:
    """Cuenta las filas de `visits` (para comprobar que nada se escribio)."""
    return int(await fetch_scalar(conn, "SELECT count(*) FROM visits"))


async def nonexistent_visit_id(conn: AsyncConnection) -> int:
    """Devuelve un id de visita que no existe."""
    highest = await fetch_scalar(conn, "SELECT coalesce(max(id), 0) FROM visits")
    return int(highest) + 1000
