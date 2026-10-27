"""Utilidades de apoyo para los tests de FEAT-017 (estantes y celdas).

La escritura esta separada por recurso: `/shelves` gestiona el estante y
`/cells` gestiona la celda con su `shelf_id`. `GET /shelves` sigue devolviendo
las celdas anidadas, por eso `ITEM_KEYS` las incluye.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import auth_headers, cached_password_hash, create_auth_user, token_for
from tests.db_support import create_brand, create_drug_type, create_shelf, unique_suffix
from tests.db_support import create_cell as create_cell_row
from tests.users_support import assert_error_envelope

SHELVES_PATH = "/api/v1/shelves"
CELLS_PATH = "/api/v1/cells"

#: Claves exactas de `ShelfRead` (FEAT-017).
ITEM_KEYS = {"id", "name", "description", "is_active", "created_at", "updated_at", "cells"}

#: Claves exactas de `CellRead` (FEAT-017).
CELL_KEYS = {
    "id",
    "shelf_id",
    "section",
    "cell",
    "description",
    "is_active",
    "created_at",
    "updated_at",
}

#: Claves del contrato anterior que ya no deben aparecer.
FORBIDDEN_KEYS = {"cell_id", "shelf", "section_cell"}

#: Mensajes de negocio exactos que el envelope devuelve como `detail`.
SHELF_EXISTS_DETAIL = "El estante ya existe"
SHELF_NOT_FOUND_DETAIL = "El estante no existe"
CELL_NOT_FOUND_DETAIL = "La celda no existe"
CELL_POSITION_EXISTS_DETAIL = "Ya existe una celda en esa posición del estante"
CELL_HAS_MEDICATIONS_DETAIL = "La celda tiene medicamentos asociados"


def shelf_path(shelf_id: int) -> str:
    """Ruta de detalle/actualización/desactivación de un estante."""
    return f"{SHELVES_PATH}/{shelf_id}"


def cell_path(cell_id: int) -> str:
    """Ruta de detalle/actualización/desactivación de una celda."""
    return f"{CELLS_PATH}/{cell_id}"


def cell_body(
    tag: str,
    *,
    shelf_id: int,
    section: str | None = None,
    cell: str | None = None,
    description: str | None = None,
    is_active: bool = True,
) -> dict[str, Any]:
    """Body de una celda con posición única derivada de `tag`."""
    return {
        "shelf_id": shelf_id,
        "section": section if section is not None else f"S-{tag}",
        "cell": cell if cell is not None else f"C-{tag}",
        "description": description,
        "is_active": is_active,
    }


def shelf_body(
    tag: str,
    *,
    name: str | None = None,
    description: str | None = None,
    is_active: bool = True,
) -> dict[str, Any]:
    """Body completo para `POST /shelves`; no incluye celdas (R-6)."""
    return {
        "name": name if name is not None else f"Estante {tag}",
        "description": description,
        "is_active": is_active,
    }


def request_body_schema(spec: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    """Resuelve el schema JSON del `requestBody` de una operación del OpenAPI."""
    content = operation["requestBody"]["content"]["application/json"]["schema"]
    reference = content.get("$ref")
    if reference is None:
        return content
    return spec["components"]["schemas"][reference.rsplit("/", 1)[-1]]


async def admin_actor(conn: AsyncConnection) -> tuple[int, dict[str, str]]:
    """Crea un ADMIN activo y devuelve su id y sus cabeceras Bearer."""
    suffix = unique_suffix()
    user_id = await create_auth_user(
        conn,
        username=f"sactor-{suffix}",
        email=f"sactor.{suffix}@example.test",
        role="ADMIN",
        password_hash=cached_password_hash(),
    )
    return user_id, auth_headers(token_for(user_id))


async def medico_actor(conn: AsyncConnection) -> tuple[int, dict[str, str]]:
    """Crea un MEDICO activo y devuelve su id y sus cabeceras Bearer."""
    suffix = unique_suffix()
    user_id = await create_auth_user(
        conn,
        username=f"smedico_{suffix}",
        email=f"smedico_{suffix}@example.test",
        role="MEDICO",
        password_hash=cached_password_hash(),
    )
    return user_id, auth_headers(token_for(user_id, role="MEDICO"))


async def fetch_shelf_row(conn: AsyncConnection, shelf_id: int) -> dict[str, Any] | None:
    """Lee la fila completa de un estante, incluidas las fechas."""
    result = await conn.execute(
        text(
            """
            SELECT id, name, description, is_active, created_at, updated_at
            FROM shelves
            WHERE id = :shelf_id
            """
        ),
        {"shelf_id": shelf_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row is not None else None


async def fetch_cell_row(conn: AsyncConnection, cell_id: int) -> dict[str, Any] | None:
    """Lee la fila completa de una celda, incluidas las fechas."""
    result = await conn.execute(
        text(
            """
            SELECT id, shelf_id, section, cell, description, is_active, created_at, updated_at
            FROM cells
            WHERE id = :cell_id
            """
        ),
        {"cell_id": cell_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row is not None else None


async def cell_positions(conn: AsyncConnection, shelf_id: int) -> dict[tuple[str, str], int]:
    """Devuelve `{(section, cell): id}` de las celdas de un estante."""
    result = await conn.execute(
        text("SELECT id, section, cell FROM cells WHERE shelf_id = :shelf_id"),
        {"shelf_id": shelf_id},
    )
    return {(row[1], row[2]): row[0] for row in result.all()}


async def count_shelves(conn: AsyncConnection) -> int:
    """Cuenta las filas de `shelves` en la transacción."""
    result = await conn.execute(text("SELECT count(*) FROM shelves"))
    return int(result.scalar_one())


async def count_cells(conn: AsyncConnection) -> int:
    """Cuenta las filas de `cells` en la transacción."""
    result = await conn.execute(text("SELECT count(*) FROM cells"))
    return int(result.scalar_one())


async def nonexistent_shelf_id(conn: AsyncConnection) -> int:
    """Devuelve un id que no existe en `shelves`."""
    result = await conn.execute(text("SELECT coalesce(max(id), 0) + 100000 FROM shelves"))
    return int(result.scalar_one())


async def nonexistent_cell_id(conn: AsyncConnection) -> int:
    """Devuelve un id que no existe en `cells`."""
    result = await conn.execute(text("SELECT coalesce(max(id), 0) + 100000 FROM cells"))
    return int(result.scalar_one())


async def create_cell(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    shelf_id: int,
    tag: str | None = None,
    section: str | None = None,
    cell: str | None = None,
    description: str | None = None,
    is_active: bool = True,
) -> Response:
    """Crea una celda por `POST /cells` con una posición única derivada de `tag`."""
    suffix = tag or unique_suffix()
    return await client.post(
        CELLS_PATH,
        headers=headers,
        json=cell_body(
            suffix,
            shelf_id=shelf_id,
            section=section,
            cell=cell,
            description=description,
            is_active=is_active,
        ),
    )


async def create_shelf_with_cells(
    conn: AsyncConnection,
    *,
    tag: str | None = None,
    name: str | None = None,
    description: str | None = None,
    is_active: bool = True,
    positions: tuple[tuple[str, str], ...] = (("S", "C"),),
) -> tuple[int, list[int]]:
    """Inserta un estante con sus celdas por SQL directo y devuelve sus ids.

    La escritura por API vive en `POST /cells`; este atajo usa la base para
    preparar el estado de partida de los tests sin pasar por HTTP.
    """
    suffix = tag or unique_suffix()
    shelf_id = await create_shelf(
        conn,
        name or f"Estante {suffix}",
        description=description,
        is_active=is_active,
    )
    cell_ids: list[int] = []
    for section, cell in positions:
        cell_ids.append(
            await create_cell_row(
                conn,
                shelf_id=shelf_id,
                section=f"{section}-{suffix}",
                cell=f"{cell}-{suffix}",
            )
        )
    return shelf_id, cell_ids


async def create_medication_for_cell(
    conn: AsyncConnection, cell_id: int, *, suffix: str | None = None
) -> int:
    """Crea un medicamento ubicado en la celda indicada y devuelve su id."""
    tag = suffix or unique_suffix()
    drug_type_id = await create_drug_type(conn, f"Tipo {tag}")
    brand_id = await create_brand(conn, f"Marca {tag}")
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


async def fetch_medication_cell(conn: AsyncConnection, medication_id: int) -> int | None:
    """Devuelve el `cell_id` (FK a `cells.id`) de un medicamento."""
    result = await conn.execute(
        text("SELECT cell_id FROM medications WHERE id = :medication_id"),
        {"medication_id": medication_id},
    )
    return result.scalar_one_or_none()


def error_fields(response: Response) -> set[str]:
    """Devuelve el conjunto de `field` de una respuesta 422."""
    assert response.status_code == 422, response.text
    return {item["field"] for item in response.json()["errors"]}


__all__ = [
    "CELLS_PATH",
    "CELL_HAS_MEDICATIONS_DETAIL",
    "CELL_KEYS",
    "CELL_NOT_FOUND_DETAIL",
    "CELL_POSITION_EXISTS_DETAIL",
    "FORBIDDEN_KEYS",
    "ITEM_KEYS",
    "SHELF_EXISTS_DETAIL",
    "SHELF_NOT_FOUND_DETAIL",
    "SHELVES_PATH",
    "admin_actor",
    "assert_error_envelope",
    "cell_body",
    "cell_path",
    "cell_positions",
    "count_cells",
    "count_shelves",
    "create_cell",
    "create_cell_row",
    "create_medication_for_cell",
    "create_shelf_with_cells",
    "error_fields",
    "fetch_cell_row",
    "fetch_medication_cell",
    "fetch_shelf_row",
    "medico_actor",
    "nonexistent_cell_id",
    "nonexistent_shelf_id",
    "request_body_schema",
    "shelf_body",
    "shelf_path",
]
