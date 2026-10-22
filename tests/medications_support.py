"""Utilidades de apoyo para los tests de FEAT-010 (medicamentos)."""

from __future__ import annotations

from typing import Any

from httpx import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import auth_headers, cached_password_hash, create_auth_user, token_for
from tests.db_support import create_brand, create_drug_type, create_location, unique_suffix
from tests.users_support import assert_error_envelope

MEDICATIONS_PATH = "/api/v1/medications"

#: Claves exactas de `MedicationRead` (FEAT-010 D-4).
ITEM_KEYS = {
    "id",
    "description",
    "drug_type_id",
    "brand_id",
    "location_id",
    "dose",
    "is_active",
}

#: Claves que nunca deben aparecer en una respuesta con medicamento.
FORBIDDEN_KEYS = {"created_at", "updated_at", "drug_type", "brand", "location"}


def medication_path(medication_id: int) -> str:
    """Ruta de detalle/actualización/desactivación de un medicamento."""
    return f"{MEDICATIONS_PATH}/{medication_id}"


async def make_catalogs(
    conn: AsyncConnection, *, suffix: str | None = None
) -> tuple[int, int, int]:
    """Crea un tipo, una marca y una ubicación activos y devuelve sus ids."""
    tag = suffix or unique_suffix()
    drug_type_id = await create_drug_type(conn, f"Tipo {tag}")
    brand_id = await create_brand(conn, f"Marca {tag}")
    location_id = await create_location(conn, f"Ubicación {tag}")
    return drug_type_id, brand_id, location_id


def medication_body(
    tag: str,
    drug_type_id: int,
    brand_id: int,
    location_id: int,
    *,
    description: str | None = None,
    dose: str | None = None,
    is_active: bool = True,
) -> dict[str, Any]:
    """Body completo para `POST`/`PUT /medications`."""
    return {
        "description": description if description is not None else f"Med {tag}",
        "drug_type_id": drug_type_id,
        "brand_id": brand_id,
        "location_id": location_id,
        "dose": dose if dose is not None else f"{tag[:4]} mg",
        "is_active": is_active,
    }


async def admin_actor(conn: AsyncConnection) -> tuple[int, dict[str, str]]:
    """Crea un ADMIN activo y devuelve su id y sus cabeceras Bearer."""
    suffix = unique_suffix()
    user_id = await create_auth_user(
        conn,
        username=f"mactor-{suffix}",
        email=f"mactor.{suffix}@example.test",
        role="ADMIN",
        password_hash=cached_password_hash(),
    )
    return user_id, auth_headers(token_for(user_id))


async def medico_actor(conn: AsyncConnection) -> tuple[int, dict[str, str]]:
    """Crea un MEDICO activo y devuelve su id y sus cabeceras Bearer."""
    suffix = unique_suffix()
    user_id = await create_auth_user(
        conn,
        username=f"mmedico_{suffix}",
        email=f"mmedico_{suffix}@example.test",
        role="MEDICO",
        password_hash=cached_password_hash(),
    )
    return user_id, auth_headers(token_for(user_id, role="MEDICO"))


async def fetch_medication_row(conn: AsyncConnection, medication_id: int) -> dict[str, Any] | None:
    """Lee la fila completa de un medicamento, incluidas las fechas."""
    result = await conn.execute(
        text(
            """
            SELECT id, description, drug_type_id, brand_id, location_id,
                   dose, is_active, created_at, updated_at
            FROM medications
            WHERE id = :medication_id
            """
        ),
        {"medication_id": medication_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row is not None else None


async def create_medication_row(
    conn: AsyncConnection,
    description: str | None = None,
    *,
    drug_type_id: int | None = None,
    brand_id: int | None = None,
    location_id: int | None = None,
    dose: str = "500 mg",
    is_active: bool = True,
) -> int:
    """Inserta un medicamento por SQL directo y devuelve su id."""
    tag = unique_suffix()
    resolved_drug = drug_type_id if drug_type_id is not None else await create_drug_type(conn)
    resolved_brand = brand_id if brand_id is not None else await create_brand(conn)
    resolved_location = location_id if location_id is not None else await create_location(conn)
    result = await conn.execute(
        text(
            """
            INSERT INTO medications
                (description, drug_type_id, brand_id, location_id, dose, is_active)
            VALUES (:description, :drug_type_id, :brand_id, :location_id, :dose, :is_active)
            RETURNING id
            """
        ),
        {
            "description": description or f"Med {tag}",
            "drug_type_id": resolved_drug,
            "brand_id": resolved_brand,
            "location_id": resolved_location,
            "dose": dose,
            "is_active": is_active,
        },
    )
    return int(result.scalar_one())


async def count_medications(conn: AsyncConnection) -> int:
    """Cuenta las filas de `medications` en la transacción."""
    result = await conn.execute(text("SELECT count(*) FROM medications"))
    return int(result.scalar_one())


async def count_identity(conn: AsyncConnection, description: str, brand_id: int, dose: str) -> int:
    """Cuenta filas con una identidad dada (CITEXT: case-insensitive)."""
    result = await conn.execute(
        text(
            "SELECT count(*) FROM medications "
            "WHERE description = :description AND brand_id = :brand_id AND dose = :dose"
        ),
        {"description": description, "brand_id": brand_id, "dose": dose},
    )
    return int(result.scalar_one())


async def nonexistent_medication_id(conn: AsyncConnection) -> int:
    """Devuelve un id que no existe en `medications`."""
    result = await conn.execute(text("SELECT coalesce(max(id), 0) + 100000 FROM medications"))
    return int(result.scalar_one())


async def nonexistent_catalog_id(conn: AsyncConnection, table: str) -> int:
    """Devuelve un id que no existe en la tabla de catálogo indicada."""
    result = await conn.execute(text(f"SELECT coalesce(max(id), 0) + 100000 FROM {table}"))
    return int(result.scalar_one())


def error_fields(response: Response) -> set[str]:
    """Devuelve el conjunto de `field` de una respuesta 422."""
    assert response.status_code == 422, response.text
    return {item["field"] for item in response.json()["errors"]}


__all__ = [
    "FORBIDDEN_KEYS",
    "ITEM_KEYS",
    "MEDICATIONS_PATH",
    "admin_actor",
    "assert_error_envelope",
    "count_identity",
    "count_medications",
    "create_medication_row",
    "error_fields",
    "fetch_medication_row",
    "make_catalogs",
    "medication_body",
    "medication_path",
    "medico_actor",
    "nonexistent_catalog_id",
    "nonexistent_medication_id",
]
