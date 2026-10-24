"""Utilidades de apoyo para los tests de FEAT-011 (pacientes)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import auth_headers, cached_password_hash, create_auth_user, token_for
from tests.db_support import create_patient, fetch_scalar, unique_cedula, unique_suffix
from tests.users_support import assert_error_envelope, error_fields

PATIENTS_PATH = "/api/v1/patients"

#: Claves exactas de `PatientRead` (FEAT-011 D-4).
ITEM_KEYS = {
    "id",
    "name",
    "cedula",
    "carnet",
    "patient_type",
    "is_active",
}

#: Claves que nunca deben aparecer en una respuesta con paciente.
FORBIDDEN_KEYS = {"created_at", "updated_at", "visits", "doctor"}

__all__ = [
    "FORBIDDEN_KEYS",
    "ITEM_KEYS",
    "PATIENTS_PATH",
    "admin_actor",
    "assert_error_envelope",
    "count_patients",
    "create_patient_row",
    "error_fields",
    "fetch_patient_row",
    "medico_actor",
    "nonexistent_patient_id",
    "patient_body",
    "patient_path",
    "set_patient_active",
    "unique_cedula",
    "unique_suffix",
]


def patient_path(patient_id: int) -> str:
    """Ruta de detalle/actualizacion/desactivacion de un paciente."""
    return f"{PATIENTS_PATH}/{patient_id}"


def patient_body(
    tag: str,
    *,
    name: str | None = None,
    cedula: str | None = None,
    carnet: str | None = None,
    patient_type: str = "EMPLEADO",
    is_active: bool = True,
) -> dict[str, Any]:
    """Body completo para `POST`/`PUT /patients`."""
    return {
        "name": name if name is not None else f"Paciente {tag}",
        "cedula": cedula if cedula is not None else unique_cedula(),
        "carnet": carnet,
        "patient_type": patient_type,
        "is_active": is_active,
    }


async def admin_actor(conn: AsyncConnection) -> tuple[int, dict[str, str]]:
    """Crea un ADMIN activo y devuelve su id y sus cabeceras Bearer."""
    suffix = unique_suffix()
    user_id = await create_auth_user(
        conn,
        username=f"pactor-{suffix}",
        email=f"pactor.{suffix}@example.test",
        role="ADMIN",
        password_hash=cached_password_hash(),
    )
    return user_id, auth_headers(token_for(user_id))


async def medico_actor(conn: AsyncConnection) -> tuple[int, dict[str, str]]:
    """Crea un MEDICO activo y devuelve su id y sus cabeceras Bearer."""
    suffix = unique_suffix()
    user_id = await create_auth_user(
        conn,
        username=f"pmedico_{suffix}",
        email=f"pmedico_{suffix}@example.test",
        role="MEDICO",
        password_hash=cached_password_hash(),
    )
    return user_id, auth_headers(token_for(user_id, role="MEDICO"))


async def fetch_patient_row(conn: AsyncConnection, patient_id: int) -> dict[str, Any] | None:
    """Lee la fila completa de un paciente, incluidas las fechas."""
    result = await conn.execute(
        text(
            """
            SELECT id, name, cedula, carnet, patient_type, is_active,
                   created_at, updated_at
            FROM patients
            WHERE id = :patient_id
            """
        ),
        {"patient_id": patient_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row is not None else None


async def count_patients(conn: AsyncConnection) -> int:
    """Cuenta las filas de `patients`."""
    return int(await fetch_scalar(conn, "SELECT count(*) FROM patients"))


async def set_patient_active(conn: AsyncConnection, patient_id: int, value: bool) -> None:
    """Fija `is_active` directamente para preparar escenarios de listado."""
    await conn.execute(
        text("UPDATE patients SET is_active = :value WHERE id = :patient_id"),
        {"value": value, "patient_id": patient_id},
    )


async def create_patient_row(
    conn: AsyncConnection,
    *,
    name: str | None = None,
    cedula: str | None = None,
    carnet: str | None = None,
    patient_type: str = "EMPLEADO",
    is_active: bool = True,
) -> int:
    """Inserta un paciente por SQL y devuelve su id."""
    patient_id = await create_patient(
        conn,
        name=name,
        cedula=cedula,
        patient_type=patient_type,
        carnet=carnet,
    )
    if not is_active:
        await set_patient_active(conn, patient_id, False)
    return patient_id


async def nonexistent_patient_id(conn: AsyncConnection) -> int:
    """Devuelve un id de paciente que no existe."""
    highest = await fetch_scalar(conn, "SELECT coalesce(max(id), 0) FROM patients")
    return int(highest) + 1000
