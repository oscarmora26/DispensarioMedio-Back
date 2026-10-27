"""Utilidades de apoyo para los tests de FEAT-012 (medicos)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import auth_headers, cached_password_hash, create_auth_user, token_for
from tests.db_support import (
    create_patient,
    create_visit,
    fetch_scalar,
    insert_visit_raw,
    unique_cedula,
    unique_suffix,
)
from tests.users_support import assert_error_envelope, error_fields

DOCTORS_PATH = "/api/v1/doctors"

#: Claves exactas de `DoctorRead` (FEAT-012 D-2 + FEAT-018 `user`).
ITEM_KEYS = {
    "id",
    "name",
    "cedula",
    "lab_shift",
    "specialty",
    "is_active",
    "user",
}

#: Claves que nunca deben aparecer en una respuesta con medico.
FORBIDDEN_KEYS = {"created_at", "updated_at", "visits"}

__all__ = [
    "DOCTORS_PATH",
    "FORBIDDEN_KEYS",
    "ITEM_KEYS",
    "admin_actor",
    "assert_error_envelope",
    "count_doctors",
    "create_doctor_row",
    "doctor_body",
    "doctor_path",
    "error_fields",
    "fetch_doctor_row",
    "medico_actor",
    "nonexistent_doctor_id",
    "set_doctor_active",
    "unique_cedula",
    "unique_suffix",
    "visit_for_doctor",
]


def doctor_path(doctor_id: int) -> str:
    """Ruta de detalle/actualizacion/desactivacion de un medico."""
    return f"{DOCTORS_PATH}/{doctor_id}"


def doctor_body(
    tag: str,
    *,
    name: str | None = None,
    cedula: str | None = None,
    lab_shift: str = "Matutina",
    specialty: str = "Medicina General",
    is_active: bool = True,
) -> dict[str, Any]:
    """Body completo para `POST`/`PUT /doctors`."""
    return {
        "name": name if name is not None else f"Dr. {tag}",
        "cedula": cedula if cedula is not None else unique_cedula(),
        "lab_shift": lab_shift,
        "specialty": specialty,
        "is_active": is_active,
    }


async def admin_actor(conn: AsyncConnection) -> tuple[int, dict[str, str]]:
    """Crea un ADMIN activo y devuelve su id y sus cabeceras Bearer."""
    suffix = unique_suffix()
    user_id = await create_auth_user(
        conn,
        username=f"dactor-{suffix}",
        email=f"dactor.{suffix}@example.test",
        role="ADMIN",
        password_hash=cached_password_hash(),
    )
    return user_id, auth_headers(token_for(user_id))


async def medico_actor(
    conn: AsyncConnection,
    *,
    doctor_id: int | None = None,
) -> tuple[int, dict[str, str]]:
    """Crea un MEDICO activo (asociado a `doctor_id` si se indica) con cabeceras."""
    suffix = unique_suffix()
    user_id = await create_auth_user(
        conn,
        username=f"dmedico_{suffix}",
        email=f"dmedico_{suffix}@example.test",
        role="MEDICO",
        password_hash=cached_password_hash(),
        doctor_id=doctor_id,
    )
    return user_id, auth_headers(token_for(user_id, role="MEDICO", doctor_id=doctor_id))


async def fetch_doctor_row(conn: AsyncConnection, doctor_id: int) -> dict[str, Any] | None:
    """Lee la fila completa de un medico, incluidas las fechas."""
    result = await conn.execute(
        text(
            """
            SELECT id, name, cedula, lab_shift, specialty, is_active,
                   created_at, updated_at
            FROM doctors
            WHERE id = :doctor_id
            """
        ),
        {"doctor_id": doctor_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row is not None else None


async def count_doctors(conn: AsyncConnection) -> int:
    """Cuenta las filas de `doctors`."""
    return int(await fetch_scalar(conn, "SELECT count(*) FROM doctors"))


async def set_doctor_active(conn: AsyncConnection, doctor_id: int, value: bool) -> None:
    """Fija `is_active` directamente para preparar escenarios."""
    await conn.execute(
        text("UPDATE doctors SET is_active = :value WHERE id = :doctor_id"),
        {"value": value, "doctor_id": doctor_id},
    )


async def create_doctor_row(
    conn: AsyncConnection,
    *,
    name: str | None = None,
    cedula: str | None = None,
    lab_shift: str = "Matutina",
    specialty: str = "Medicina General",
    is_active: bool = True,
) -> int:
    """Inserta un medico por SQL y devuelve su id."""
    result = await conn.execute(
        text(
            """
            INSERT INTO doctors (name, cedula, lab_shift, specialty)
            VALUES (:name, :cedula, :lab_shift, :specialty)
            RETURNING id
            """
        ),
        {
            "name": name or f"Dr. Prueba {unique_suffix()}",
            "cedula": cedula or unique_cedula(),
            "lab_shift": lab_shift,
            "specialty": specialty,
        },
    )
    doctor_id = int(result.scalar_one())
    if not is_active:
        await set_doctor_active(conn, doctor_id, False)
    return doctor_id


async def visit_for_doctor(
    conn: AsyncConnection,
    doctor_id: int,
    *,
    status: str = "REGISTRADA",
) -> int:
    """Crea una visita del medico indicado (REGISTRADA o ANULADA valida)."""
    if status == "REGISTRADA":
        return await create_visit(conn, doctor_id=doctor_id)

    annulled_by = await create_auth_user(
        conn,
        username=f"dannul_{unique_suffix()}",
        role="ADMIN",
        password_hash=cached_password_hash(),
    )
    return await insert_visit_raw(
        conn,
        doctor_id=doctor_id,
        patient_id=await create_patient(conn),
        created_by=annulled_by,
        status="ANULADA",
        annulled_by=annulled_by,
        annulled_at=datetime.now(UTC),
        annulment_reason="Registrada por error",
    )


async def nonexistent_doctor_id(conn: AsyncConnection) -> int:
    """Devuelve un id de medico que no existe."""
    highest = await fetch_scalar(conn, "SELECT coalesce(max(id), 0) FROM doctors")
    return int(highest) + 1000
