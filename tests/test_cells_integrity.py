"""Integridad, envelope uniforme y OpenAPI de celdas (FEAT-017, R-12)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.main import app
from tests.auth_support import auth_headers, cached_password_hash, create_auth_user, token_for
from tests.db_support import unique_suffix
from tests.shelves_support import (
    CELLS_PATH,
    admin_actor,
    assert_error_envelope,
    cell_body,
    cell_path,
    create_cell,
    create_medication_for_cell,
    create_shelf_with_cells,
    fetch_medication_cell,
    medico_actor,
    nonexistent_cell_id,
    nonexistent_shelf_id,
    request_body_schema,
)


class _ConstraintViolation(Exception):
    """Imita la excepción de `asyncpg` con el nombre de la constraint."""

    def __init__(self, constraint: str) -> None:
        super().__init__(f'duplicate key value violates unique constraint "{constraint}"')
        self.constraint_name = constraint
        self.sqlstate = "23505"


async def test_integrity_error_on_commit_becomes_409_envelope(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R-12: una violación de integridad ⇒ 409 `integrity_error`, nunca un traceback."""
    _, headers = await admin_actor(db_conn)
    shelf_id, _ = await create_shelf_with_cells(db_conn, tag=unique_suffix(), positions=())

    async def failing_commit(session: AsyncSession) -> None:
        raise IntegrityError("INSERT INTO cells ...", {}, _ConstraintViolation("uq_cells_position"))

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)

    response = await auth_client.post(
        CELLS_PATH, headers=headers, json=cell_body(unique_suffix(), shelf_id=shelf_id)
    )
    body = assert_error_envelope(response, 409, "integrity_error")
    assert body["detail"] == "La operación entra en conflicto con el estado actual de los datos"
    assert body["errors"] == []

    monkeypatch.undo()
    usable = await auth_client.get(CELLS_PATH, headers=headers)
    assert usable.status_code == 200, usable.text
    assert "items" in usable.json()


async def test_catalog_codes_use_the_exact_envelope(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """Cada código del catálogo respeta `{detail, code, errors}` con `errors` lista."""
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    tag = unique_suffix()
    shelf_id, (occupied_id,) = await create_shelf_with_cells(
        db_conn, tag=tag, positions=(("A", "1"),)
    )
    created = await create_cell(auth_client, admin_headers, shelf_id=shelf_id, tag=tag)
    assert created.status_code == 201, created.text
    cell_id = created.json()["id"]
    occupied = await auth_client.get(cell_path(occupied_id), headers=admin_headers)
    assert occupied.status_code == 200, occupied.text
    missing_cell = await nonexistent_cell_id(db_conn)
    missing_shelf = await nonexistent_shelf_id(db_conn)
    inactive_id = await create_auth_user(
        db_conn,
        username=f"sinactivo_{unique_suffix()}",
        role="ADMIN",
        is_active=False,
        password_hash=cached_password_hash(),
    )
    inactive_headers = auth_headers(token_for(inactive_id))

    cases = [
        (await auth_client.get(CELLS_PATH), 401, "not_authenticated"),
        (
            await auth_client.get(CELLS_PATH, headers={"Authorization": "Bearer basura"}),
            401,
            "invalid_token",
        ),
        (await auth_client.get(CELLS_PATH, headers=inactive_headers), 403, "inactive_user"),
        (
            await auth_client.post(
                CELLS_PATH, headers=medico_headers, json=cell_body(tag, shelf_id=shelf_id)
            ),
            403,
            "forbidden",
        ),
        (
            await auth_client.get(cell_path(missing_cell), headers=admin_headers),
            404,
            "cell_not_found",
        ),
        (
            await auth_client.post(
                CELLS_PATH,
                headers=admin_headers,
                json=cell_body(unique_suffix(), shelf_id=missing_shelf),
            ),
            404,
            "shelf_not_found",
        ),
        (
            await auth_client.post(
                CELLS_PATH,
                headers=admin_headers,
                json=cell_body(
                    tag,
                    shelf_id=shelf_id,
                    section=str(occupied.json()["section"]).lower(),
                    cell=str(occupied.json()["cell"]).lower(),
                ),
            ),
            409,
            "cell_position_exists",
        ),
        (
            await auth_client.post(
                CELLS_PATH,
                headers=admin_headers,
                json={**cell_body(tag, shelf_id=shelf_id), "x": 1},
            ),
            422,
            "validation_error",
        ),
        (
            await auth_client.get(f"{CELLS_PATH}/abc", headers=admin_headers),
            422,
            "validation_error",
        ),
        (
            await auth_client.put(
                cell_path(missing_cell),
                headers=admin_headers,
                json={
                    "shelf_id": shelf_id,
                    "section": f"S-{tag}",
                    "cell": f"C-{tag}",
                    "description": None,
                    "is_active": True,
                },
            ),
            404,
            "cell_not_found",
        ),
        (
            await auth_client.delete(cell_path(missing_cell), headers=admin_headers),
            404,
            "cell_not_found",
        ),
        (
            await auth_client.delete(cell_path(cell_id), headers=medico_headers),
            403,
            "forbidden",
        ),
    ]
    for response, status, code in cases:
        body = assert_error_envelope(response, status, code)
        assert isinstance(body["errors"], list)


async def test_delete_with_medications_envelope_is_exact(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """R-8: `DELETE` de una celda con medicamentos documenta el envelope 409."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    _, (cell_id,) = await create_shelf_with_cells(db_conn, tag=tag, positions=(("A", "1"),))
    medication_id = await create_medication_for_cell(db_conn, cell_id, suffix=tag)

    response = await auth_client.delete(cell_path(cell_id), headers=headers)
    body = assert_error_envelope(response, 409, "cell_has_medications")
    assert body["errors"][0]["field"] == "cell_id"

    # El medicamento sigue asociado y la celda sigue activa.
    detail = await auth_client.get(cell_path(cell_id), headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["is_active"] is True
    assert await fetch_medication_cell(db_conn, medication_id) == cell_id


def test_openapi_declares_the_cells_contract() -> None:
    """Los cinco endpoints viven bajo `/api/v1/cells`, con tag, seguridad y códigos."""
    spec = app.openapi()
    paths = spec["paths"]

    cell_paths = sorted(path for path in paths if path.startswith("/api/v1/cells"))
    assert cell_paths == ["/api/v1/cells", "/api/v1/cells/{cell_id}"]

    tags = sorted(
        {
            tag
            for path in cell_paths
            for method in paths[path].values()
            for tag in method.get("tags", [])
        }
    )
    assert tags == ["cells"]

    operations = [
        (path, verb, method) for path in cell_paths for verb, method in paths[path].items()
    ]
    assert len(operations) == 5
    for path, verb, method in operations:
        assert method.get("security"), (path, verb)
        assert method.get("summary"), (path, verb)

    expected = {
        ("/api/v1/cells", "get"): ["200", "422"],
        ("/api/v1/cells", "post"): ["201", "409", "422"],
        ("/api/v1/cells/{cell_id}", "get"): ["200", "422"],
        ("/api/v1/cells/{cell_id}", "put"): ["200", "409", "422"],
        ("/api/v1/cells/{cell_id}", "delete"): ["204", "409", "422"],
    }
    for (path, verb), codes in expected.items():
        assert sorted(paths[path][verb]["responses"]) == codes, (path, verb)

    # `CellRead` documenta la forma exacta de la celda.
    read_schema = spec["components"]["schemas"]["CellRead"]
    assert set(read_schema["properties"]) == {
        "id",
        "shelf_id",
        "section",
        "cell",
        "description",
        "is_active",
        "created_at",
        "updated_at",
    }


def test_openapi_cells_write_bodies_require_the_shelf_id() -> None:
    """`POST`/`PUT` de `/cells` documentan los cinco campos de escritura."""
    spec = app.openapi()
    paths = spec["paths"]

    for path, verb in (("/api/v1/cells", "post"), ("/api/v1/cells/{cell_id}", "put")):
        schema = request_body_schema(spec, paths[path][verb])
        assert set(schema["properties"]) == {
            "shelf_id",
            "section",
            "cell",
            "description",
            "is_active",
        }, (path, verb)
        assert schema.get("additionalProperties") is False, (path, verb)
        assert "shelf_id" in schema["properties"], (path, verb)
