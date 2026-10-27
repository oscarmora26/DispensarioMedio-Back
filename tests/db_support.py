"""Utilidades compartidas por los tests de integracion contra PostgreSQL.

Todas las funciones esperan una `AsyncConnection` con una transaccion abierta
por el fixture `db_conn`; nada de lo que se escribe aqui debe persistir.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

# Carga `.env` para que TEST_DATABASE_URL documentada en `.env.example` llegue a
# os.environ. Sin override: una variable de entorno real siempre tiene prioridad.
load_dotenv()

Params = Mapping[str, Any]


def resolve_test_database_url() -> str:
    """URL de la base que usa la suite (paso 3: BD dedicada `test-db`).

    `TEST_DATABASE_URL` del entorno cuando existe y no esta vacia; si no,
    `get_settings().database_url` (BD de desarrollo, comportamiento previo).
    """
    override = os.environ.get("TEST_DATABASE_URL", "").strip()
    if override:
        return override
    return get_settings().database_url


async def require_migrated_database(connection: AsyncConnection) -> None:
    """Falla con un mensaje accionable si la BD de la suite no esta migrada.

    Solo verifica `alembic_version`; nunca ejecuta migraciones dentro de los
    tests. Los errores de conexion se propagan tal cual (no son falta de
    migracion y su mensaje original es mas util).
    """
    version_table = await connection.scalar(text("SELECT to_regclass('public.alembic_version')"))
    versions: list[object] = []
    if version_table is not None:
        result = await connection.execute(text("SELECT version_num FROM alembic_version"))
        versions = list(result.all())
    if version_table is None or not versions:
        raise RuntimeError(
            "La base de datos de tests no esta migrada (falta la tabla "
            "'alembic_version' o no tiene ninguna version aplicada). Aplica "
            "las migraciones con `alembic upgrade head` contra "
            "TEST_DATABASE_URL antes de ejecutar la suite; los tests no "
            "migran la base automaticamente."
        )


async def fetch_scalar(conn: AsyncConnection, sql: str, params: Params | None = None) -> Any:
    """Ejecuta una consulta que devuelve una unica fila/columna."""
    result = await conn.execute(text(sql), dict(params or {}))
    return result.scalar_one()


async def fetch_all(conn: AsyncConnection, sql: str, params: Params | None = None) -> list[Any]:
    """Ejecuta una consulta y devuelve todas las filas como tuplas."""
    result = await conn.execute(text(sql), dict(params or {}))
    return list(result.all())


async def fetch_scalar_in_new_connection(sql: str, params: Params | None = None) -> Any:
    """Consulta con engine propio, para tests que no pueden compartir transaccion."""
    engine = create_async_engine(resolve_test_database_url(), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return await fetch_scalar(conn, sql, params)
    finally:
        await engine.dispose()


async def insert_id(conn: AsyncConnection, sql: str, params: Params | None = None) -> int:
    """Ejecuta un INSERT ... RETURNING id y devuelve el identificador."""
    return int(await fetch_scalar(conn, sql, params))


async def assert_integrity_error(
    conn: AsyncConnection, sql: str, params: Params | None = None
) -> IntegrityError:
    """Comprueba que una sentencia viola una constraint y deja la transaccion usable."""
    savepoint = await conn.begin_nested()
    try:
        await conn.execute(text(sql), dict(params or {}))
    except IntegrityError as exc:
        await savepoint.rollback()
        return exc
    await savepoint.rollback()
    raise AssertionError(f"Se esperaba IntegrityError, pero la sentencia paso: {sql}")


async def assert_database_error(
    conn: AsyncConnection, sql: str, params: Params | None = None
) -> DBAPIError:
    """Comprueba que una sentencia falla en la base, sea IntegrityError u otra.

    Necesario para valores que PostgreSQL rechaza por typmod (p. ej. un
    `VARCHAR(11)` con 12 caracteres) antes de evaluar la constraint CHECK.
    """
    savepoint = await conn.begin_nested()
    try:
        await conn.execute(text(sql), dict(params or {}))
    except DBAPIError as exc:
        await savepoint.rollback()
        return exc
    await savepoint.rollback()
    raise AssertionError(f"Se esperaba un error de base de datos, pero la sentencia paso: {sql}")


def unique_cedula() -> str:
    """Genera una cedula de 11 digitos valida y practicamente unica."""
    return f"{uuid.uuid4().int % 10**11:011d}"


def unique_suffix() -> str:
    """Sufijo corto para nombres unicos dentro de los tests."""
    return uuid.uuid4().hex[:10]


async def get_role_id(conn: AsyncConnection, name: str = "ADMIN") -> int:
    """Devuelve el id de un rol sembrado por la migracion `seed_roles`."""
    return int(await fetch_scalar(conn, "SELECT id FROM roles WHERE name = :name", {"name": name}))


async def create_doctor(
    conn: AsyncConnection,
    *,
    name: str | None = None,
    cedula: str | None = None,
) -> int:
    """Crea un medico valido y devuelve su id."""
    return await insert_id(
        conn,
        """
        INSERT INTO doctors (name, cedula, lab_shift, specialty)
        VALUES (:name, :cedula, 'Matutino', 'Medicina general')
        RETURNING id
        """,
        {"name": name or f"Dr. Prueba {unique_suffix()}", "cedula": cedula or unique_cedula()},
    )


async def create_user(
    conn: AsyncConnection,
    *,
    role_id: int | None = None,
    doctor_id: int | None = None,
    username: str | None = None,
    email: str | None = None,
) -> int:
    """Crea un usuario valido y devuelve su id. No usa hashes reales."""
    suffix = unique_suffix()
    return await insert_id(
        conn,
        """
        INSERT INTO users (username, email, password_hash, role_id, doctor_id)
        VALUES (:username, :email, :password_hash, :role_id, :doctor_id)
        RETURNING id
        """,
        {
            "username": username or f"usuario_{suffix}",
            "email": email or f"{suffix}@example.test",
            "password_hash": "$2b$12$hash-de-prueba-no-real",
            "role_id": role_id if role_id is not None else await get_role_id(conn),
            "doctor_id": doctor_id,
        },
    )


async def create_patient(
    conn: AsyncConnection,
    *,
    name: str | None = None,
    cedula: str | None = None,
    patient_type: str = "EMPLEADO",
    carnet: str | None = None,
) -> int:
    """Crea un paciente valido y devuelve su id."""
    return await insert_id(
        conn,
        """
        INSERT INTO patients (name, cedula, patient_type, carnet)
        VALUES (:name, :cedula, :patient_type, :carnet)
        RETURNING id
        """,
        {
            "name": name or f"Paciente {unique_suffix()}",
            "cedula": cedula or unique_cedula(),
            "patient_type": patient_type,
            "carnet": carnet,
        },
    )


async def create_drug_type(conn: AsyncConnection, description: str | None = None) -> int:
    """Crea un tipo de medicamento y devuelve su id."""
    return await insert_id(
        conn,
        "INSERT INTO drug_types (description) VALUES (:description) RETURNING id",
        {"description": description or f"Tipo {unique_suffix()}"},
    )


async def create_brand(conn: AsyncConnection, description: str | None = None) -> int:
    """Crea una marca y devuelve su id."""
    return await insert_id(
        conn,
        "INSERT INTO brands (description) VALUES (:description) RETURNING id",
        {"description": description or f"Marca {unique_suffix()}"},
    )


async def create_shelf(
    conn: AsyncConnection,
    name: str | None = None,
    *,
    description: str | None = None,
    is_active: bool = True,
) -> int:
    """Crea un estante con nombre unico y devuelve su id."""
    return await insert_id(
        conn,
        """
        INSERT INTO shelves (name, description, is_active)
        VALUES (:name, :description, :is_active)
        RETURNING id
        """,
        {
            "name": name or f"Estante {unique_suffix()}",
            "description": description,
            "is_active": is_active,
        },
    )


async def create_cell(
    conn: AsyncConnection,
    *,
    shelf_id: int | None = None,
    section: str | None = None,
    cell: str | None = None,
    description: str | None = None,
    is_active: bool = True,
) -> int:
    """Crea una celda con posicion unica (estante propio si falta) y devuelve su id."""
    suffix = unique_suffix()
    return await insert_id(
        conn,
        """
        INSERT INTO cells (shelf_id, section, cell, description, is_active)
        VALUES (:shelf_id, :section, :cell, :description, :is_active)
        RETURNING id
        """,
        {
            "shelf_id": shelf_id if shelf_id is not None else await create_shelf(conn),
            "section": section or f"B-{suffix}",
            "cell": cell or f"C-{suffix}",
            "description": description,
            "is_active": is_active,
        },
    )


async def create_medication(
    conn: AsyncConnection,
    *,
    description: str | None = None,
    brand_id: int | None = None,
    drug_type_id: int | None = None,
    cell_id: int | None = None,
    dose: str = "500 mg",
) -> int:
    """Crea un medicamento (y sus catalogos si faltan) y devuelve su id."""
    return await insert_id(
        conn,
        """
        INSERT INTO medications (description, drug_type_id, brand_id, cell_id, dose)
        VALUES (:description, :drug_type_id, :brand_id, :cell_id, :dose)
        RETURNING id
        """,
        {
            "description": description or f"Medicamento {unique_suffix()}",
            "drug_type_id": drug_type_id or await create_drug_type(conn),
            "brand_id": brand_id or await create_brand(conn),
            "cell_id": cell_id or await create_cell(conn),
            "dose": dose,
        },
    )


async def create_visit(
    conn: AsyncConnection,
    *,
    doctor_id: int | None = None,
    patient_id: int | None = None,
    created_by: int | None = None,
    status: str = "REGISTRADA",
    annulled_by: int | None = None,
    annulled_at: datetime | None = None,
    annulment_reason: str | None = None,
) -> int:
    """Crea una visita valida y devuelve su id."""
    return await insert_visit_raw(
        conn,
        doctor_id=doctor_id or await create_doctor(conn),
        patient_id=patient_id or await create_patient(conn),
        created_by=created_by or await create_user(conn),
        status=status,
        annulled_by=annulled_by,
        annulled_at=annulled_at,
        annulment_reason=annulment_reason,
    )


async def insert_visit_raw(
    conn: AsyncConnection,
    *,
    doctor_id: int,
    patient_id: int,
    created_by: int,
    status: str,
    annulled_by: int | None = None,
    annulled_at: datetime | None = None,
    annulment_reason: str | None = None,
) -> int:
    """Inserta una visita con todos los campos de anulacion bajo control del test."""
    return await insert_id(
        conn,
        """
        INSERT INTO visits (
            doctor_id, patient_id, visited_at, symptoms, status,
            created_by, annulled_by, annulled_at, annulment_reason
        )
        VALUES (
            :doctor_id, :patient_id, :visited_at, 'Sintomas de prueba', :status,
            :created_by, :annulled_by, :annulled_at, :annulment_reason
        )
        RETURNING id
        """,
        {
            "doctor_id": doctor_id,
            "patient_id": patient_id,
            "visited_at": datetime.now(UTC),
            "created_by": created_by,
            "status": status,
            "annulled_by": annulled_by,
            "annulled_at": annulled_at,
            "annulment_reason": annulment_reason,
        },
    )


async def create_visit_medication(
    conn: AsyncConnection,
    *,
    visit_id: int | None = None,
    medication_id: int | None = None,
    quantity: Any = None,
) -> int:
    """Crea un detalle de visita y devuelve su id."""
    return await insert_id(
        conn,
        """
        INSERT INTO visit_medications (visit_id, medication_id, quantity, instructions)
        VALUES (:visit_id, :medication_id, :quantity, 'Tomar con agua')
        RETURNING id
        """,
        {
            "visit_id": visit_id or await create_visit(conn),
            "medication_id": medication_id or await create_medication(conn),
            "quantity": quantity,
        },
    )


async def create_audit_log(
    conn: AsyncConnection,
    *,
    user_id: int | None = None,
    action: str = "LOGIN",
    entity: str = "users",
    entity_id: int = 1,
    ip: str | None = None,
) -> int:
    """Crea una fila de auditoria y devuelve su id."""
    return await insert_id(
        conn,
        """
        INSERT INTO audit_log (user_id, action, entity, entity_id, ip)
        VALUES (:user_id, :action, :entity, :entity_id, :ip)
        RETURNING id
        """,
        {
            "user_id": user_id,
            "action": action,
            "entity": entity,
            "entity_id": entity_id,
            "ip": ip,
        },
    )
