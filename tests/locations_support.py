"""Utilidades de apoyo para los tests de FEAT-009 (ubicaciones)."""

from __future__ import annotations

from typing import Any

from httpx import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import auth_headers, cached_password_hash, create_auth_user, token_for
from tests.db_support import create_brand, create_drug_type, unique_suffix
from tests.users_support import assert_error_envelope

LOCATIONS_PATH = "/api/v1/locations"

#: Claves exactas de `LocationRead` (FEAT-009 D-4).
ITEM_KEYS = {"id", "description", "shelf", "section", "cell", "is_active"}

#: Claves que nunca deben aparecer en una respuesta con ubicación.
FORBIDDEN_KEYS = {"created_at", "updated_at"}


def location_path(location_id: int) -> str:
    """Ruta de detalle/actualización/desactivación de una ubicación."""
    return f"{LOCATIONS_PATH}/{location_id}"


def location_body(
    tag: str,
    *,
    description: str | None = None,
    shelf: str | None = None,
    section: str | None = None,
    cell: str | None = None,
    is_active: bool = True,
) -> dict[str, Any]:
    """Body completo para `POST`/`PUT /locations` con posición única."""
    return {
        "description": description or f"Estante {tag} - Tramo {tag} - Celda {tag}",
        "shelf": shelf or f"A-{tag}",
        "section": section or f"S-{tag}",
        "cell": cell or f"C-{tag}",
        "is_active": is_active,
    }


async def admin_actor(conn: AsyncConnection) -> tuple[int, dict[str, str]]:
    """Crea un ADMIN activo y devuelve su id y sus cabeceras Bearer."""
    suffix = unique_suffix()
    user_id = await create_auth_user(
        conn,
        username=f"lactor-{suffix}",
        email=f"lactor.{suffix}@example.test",
        role="ADMIN",
        password_hash=cached_password_hash(),
    )
    return user_id, auth_headers(token_for(user_id))


async def medico_actor(conn: AsyncConnection) -> tuple[int, dict[str, str]]:
    """Crea un MEDICO activo y devuelve su id y sus cabeceras Bearer."""
    suffix = unique_suffix()
    user_id = await create_auth_user(
        conn,
        username=f"lmedico_{suffix}",
        email=f"lmedico_{suffix}@example.test",
        role="MEDICO",
        password_hash=cached_password_hash(),
    )
    return user_id, auth_headers(token_for(user_id, role="MEDICO"))


async def fetch_location_row(conn: AsyncConnection, location_id: int) -> dict[str, Any] | None:
    """Lee la fila completa de una ubicación, incluidas las fechas."""
    result = await conn.execute(
        text(
            """
            SELECT id, description, shelf, section, cell, is_active, created_at, updated_at
            FROM locations
            WHERE id = :location_id
            """
        ),
        {"location_id": location_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row is not None else None


async def create_location_row(
    conn: AsyncConnection,
    description: str | None = None,
    *,
    shelf: str | None = None,
    section: str | None = None,
    cell: str | None = None,
    is_active: bool = True,
) -> int:
    """Inserta una ubicación por SQL directo y devuelve su id."""
    tag = unique_suffix()
    result = await conn.execute(
        text(
            """
            INSERT INTO locations (description, shelf, section, cell, is_active)
            VALUES (:description, :shelf, :section, :cell, :is_active)
            RETURNING id
            """
        ),
        {
            "description": description or f"Ubicación {tag}",
            "shelf": shelf or f"A-{tag}",
            "section": section or f"S-{tag}",
            "cell": cell or f"C-{tag}",
            "is_active": is_active,
        },
    )
    return int(result.scalar_one())


async def count_locations(conn: AsyncConnection) -> int:
    """Cuenta las filas de `locations` en la transacción."""
    result = await conn.execute(text("SELECT count(*) FROM locations"))
    return int(result.scalar_one())


async def nonexistent_location_id(conn: AsyncConnection) -> int:
    """Devuelve un id que no existe en `locations`."""
    result = await conn.execute(text("SELECT coalesce(max(id), 0) + 100000 FROM locations"))
    return int(result.scalar_one())


async def create_medication_for_location(
    conn: AsyncConnection, location_id: int, *, suffix: str | None = None
) -> int:
    """Crea un medicamento que referencia la ubicación indicada y devuelve su id."""
    tag = suffix or unique_suffix()
    drug_type_id = await create_drug_type(conn, f"Tipo {tag}")
    brand_id = await create_brand(conn, f"Marca {tag}")
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
    "FORBIDDEN_KEYS",
    "ITEM_KEYS",
    "LOCATIONS_PATH",
    "admin_actor",
    "assert_error_envelope",
    "count_locations",
    "create_location_row",
    "create_medication_for_location",
    "error_fields",
    "fetch_location_row",
    "location_body",
    "location_path",
    "medico_actor",
    "nonexistent_location_id",
]
