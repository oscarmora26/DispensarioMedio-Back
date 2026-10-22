"""Utilidades de apoyo para los tests de FEAT-008 (marcas)."""

from __future__ import annotations

from typing import Any

from httpx import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import auth_headers, cached_password_hash, create_auth_user, token_for
from tests.db_support import create_drug_type, create_location, unique_suffix
from tests.users_support import assert_error_envelope

BRANDS_PATH = "/api/v1/brands"

#: Claves exactas de `BrandRead` (FEAT-008 D-4).
ITEM_KEYS = {"id", "description", "is_active"}

#: Claves que nunca deben aparecer en una respuesta con marca.
FORBIDDEN_KEYS = {"created_at", "updated_at"}


def brand_path(brand_id: int) -> str:
    """Ruta de detalle/desactivacion de una marca."""
    return f"{BRANDS_PATH}/{brand_id}"


async def admin_actor(conn: AsyncConnection) -> tuple[int, dict[str, str]]:
    """Crea un ADMIN activo y devuelve su id y sus cabeceras Bearer."""
    suffix = unique_suffix()
    user_id = await create_auth_user(
        conn,
        username=f"bactor-{suffix}",
        email=f"bactor.{suffix}@example.test",
        role="ADMIN",
        password_hash=cached_password_hash(),
    )
    return user_id, auth_headers(token_for(user_id))


async def medico_actor(conn: AsyncConnection) -> tuple[int, dict[str, str]]:
    """Crea un MEDICO activo y devuelve su id y sus cabeceras Bearer."""
    suffix = unique_suffix()
    user_id = await create_auth_user(
        conn,
        username=f"bmedico_{suffix}",
        email=f"bmedico_{suffix}@example.test",
        role="MEDICO",
        password_hash=cached_password_hash(),
    )
    return user_id, auth_headers(token_for(user_id, role="MEDICO"))


async def fetch_brand_row(conn: AsyncConnection, brand_id: int) -> dict[str, Any] | None:
    """Lee la fila completa de una marca, incluidas las fechas."""
    result = await conn.execute(
        text(
            """
            SELECT id, description, is_active, created_at, updated_at
            FROM brands
            WHERE id = :brand_id
            """
        ),
        {"brand_id": brand_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row is not None else None


async def create_brand_row(
    conn: AsyncConnection,
    description: str | None = None,
    *,
    is_active: bool = True,
) -> int:
    """Inserta una marca por SQL directo y devuelve su id."""
    result = await conn.execute(
        text(
            "INSERT INTO brands (description, is_active) VALUES (:description, :is_active)"
            " RETURNING id"
        ),
        {"description": description or f"Marca {unique_suffix()}", "is_active": is_active},
    )
    return int(result.scalar_one())


async def count_brands(conn: AsyncConnection) -> int:
    """Cuenta las filas de `brands` en la transaccion."""
    result = await conn.execute(text("SELECT count(*) FROM brands"))
    return int(result.scalar_one())


async def nonexistent_brand_id(conn: AsyncConnection) -> int:
    """Devuelve un id que no existe en `brands`."""
    result = await conn.execute(text("SELECT coalesce(max(id), 0) + 100000 FROM brands"))
    return int(result.scalar_one())


async def create_medication_for_brand(
    conn: AsyncConnection, brand_id: int, *, suffix: str | None = None
) -> int:
    """Crea un medicamento que referencia la marca indicada y devuelve su id."""
    tag = suffix or unique_suffix()
    drug_type_id = await create_drug_type(conn, f"Tipo {tag}")
    location_id = await create_location(conn, f"Ubicacion {tag}")
    result = await conn.execute(
        text(
            """
            INSERT INTO medications (description, drug_type_id, brand_id, location_id, dose)
            VALUES (:description, :drug_type_id, :brand_id, :location_id, :dose)
            RETURNING id
            """
        ),
        {
            "description": f"Medicamento {tag}",
            "drug_type_id": drug_type_id,
            "brand_id": brand_id,
            "location_id": location_id,
            "dose": "500 mg",
        },
    )
    return int(result.scalar_one())


def error_fields(response: Response) -> set[str]:
    """Devuelve el conjunto de `field` de una respuesta 422."""
    assert response.status_code == 422, response.text
    return {item["field"] for item in response.json()["errors"]}


__all__ = [
    "BRANDS_PATH",
    "FORBIDDEN_KEYS",
    "ITEM_KEYS",
    "admin_actor",
    "assert_error_envelope",
    "brand_path",
    "count_brands",
    "create_brand_row",
    "create_medication_for_brand",
    "error_fields",
    "fetch_brand_row",
    "medico_actor",
    "nonexistent_brand_id",
]
