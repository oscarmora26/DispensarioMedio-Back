"""Utilidades de apoyo para los tests de FEAT-007 (tipos de farmacos)."""

from __future__ import annotations

from typing import Any

from httpx import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import auth_headers, cached_password_hash, create_auth_user, token_for
from tests.db_support import create_brand, create_cell, unique_suffix
from tests.users_support import assert_error_envelope

DRUG_TYPES_PATH = "/api/v1/drug-types"

#: Claves exactas de `DrugTypeRead` (FEAT-007 D-4).
ITEM_KEYS = {"id", "description", "is_active"}

#: Claves que nunca deben aparecer en una respuesta con tipo.
FORBIDDEN_KEYS = {"created_at", "updated_at"}


def drug_type_path(drug_type_id: int) -> str:
    """Ruta de detalle/desactivacion de un tipo de farmaco."""
    return f"{DRUG_TYPES_PATH}/{drug_type_id}"


async def admin_actor(conn: AsyncConnection) -> tuple[int, dict[str, str]]:
    """Crea un ADMIN activo y devuelve su id y sus cabeceras Bearer."""
    suffix = unique_suffix()
    user_id = await create_auth_user(
        conn,
        username=f"dtactor-{suffix}",
        email=f"dtactor.{suffix}@example.test",
        role="ADMIN",
        password_hash=cached_password_hash(),
    )
    return user_id, auth_headers(token_for(user_id))


async def medico_actor(conn: AsyncConnection) -> tuple[int, dict[str, str]]:
    """Crea un MEDICO activo y devuelve su id y sus cabeceras Bearer."""
    suffix = unique_suffix()
    user_id = await create_auth_user(
        conn,
        username=f"dtmedico_{suffix}",
        email=f"dtmedico_{suffix}@example.test",
        role="MEDICO",
        password_hash=cached_password_hash(),
    )
    return user_id, auth_headers(token_for(user_id, role="MEDICO"))


async def fetch_drug_type_row(conn: AsyncConnection, drug_type_id: int) -> dict[str, Any] | None:
    """Lee la fila completa de un tipo, incluidas las fechas."""
    result = await conn.execute(
        text(
            """
            SELECT id, description, is_active, created_at, updated_at
            FROM drug_types
            WHERE id = :drug_type_id
            """
        ),
        {"drug_type_id": drug_type_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row is not None else None


async def create_drug_type_row(
    conn: AsyncConnection,
    description: str | None = None,
    *,
    is_active: bool = True,
) -> int:
    """Inserta un tipo por SQL directo y devuelve su id."""
    result = await conn.execute(
        text(
            "INSERT INTO drug_types (description, is_active) VALUES (:description, :is_active)"
            " RETURNING id"
        ),
        {"description": description or f"Tipo {unique_suffix()}", "is_active": is_active},
    )
    return int(result.scalar_one())


async def count_drug_types(conn: AsyncConnection) -> int:
    """Cuenta las filas de `drug_types` en la transaccion."""
    result = await conn.execute(text("SELECT count(*) FROM drug_types"))
    return int(result.scalar_one())


async def nonexistent_drug_type_id(conn: AsyncConnection) -> int:
    """Devuelve un id que no existe en `drug_types`."""
    result = await conn.execute(text("SELECT coalesce(max(id), 0) + 100000 FROM drug_types"))
    return int(result.scalar_one())


async def create_medication_for_drug_type(
    conn: AsyncConnection, drug_type_id: int, *, suffix: str | None = None
) -> int:
    """Crea un medicamento que referencia al tipo indicado y devuelve su id."""
    tag = suffix or unique_suffix()
    brand_id = await create_brand(conn, f"Marca {tag}")
    cell_id = await create_cell(conn, description=f"Celda {tag}")
    result = await conn.execute(
        text(
            """
            INSERT INTO medications (description, drug_type_id, brand_id, cell_id, dose)
            VALUES (:description, :drug_type_id, :brand_id, :cell_id, :dose)
            RETURNING id
            """
        ),
        {
            "description": f"Medicamento {tag}",
            "drug_type_id": drug_type_id,
            "brand_id": brand_id,
            "cell_id": cell_id,
            "dose": "500 mg",
        },
    )
    return int(result.scalar_one())


def error_fields(response: Response) -> set[str]:
    """Devuelve el conjunto de `field` de una respuesta 422."""
    assert response.status_code == 422, response.text
    return {item["field"] for item in response.json()["errors"]}


__all__ = [
    "DRUG_TYPES_PATH",
    "FORBIDDEN_KEYS",
    "ITEM_KEYS",
    "admin_actor",
    "assert_error_envelope",
    "count_drug_types",
    "create_drug_type_row",
    "create_medication_for_drug_type",
    "drug_type_path",
    "error_fields",
    "fetch_drug_type_row",
    "medico_actor",
    "nonexistent_drug_type_id",
]
