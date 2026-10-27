"""Integridad, envelope uniforme y OpenAPI de FEAT-017 (R-12, CA-8)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.main import app
from tests.auth_support import auth_headers, cached_password_hash, create_auth_user, token_for
from tests.db_support import unique_suffix
from tests.shelves_support import (
    SHELVES_PATH,
    admin_actor,
    assert_error_envelope,
    create_shelf_with_cells,
    medico_actor,
    nonexistent_shelf_id,
    shelf_body,
    shelf_path,
)


class _ConstraintViolation(Exception):
    """Imita la excepción de `asyncpg` con el nombre de la constraint."""

    def __init__(self, constraint: str) -> None:
        super().__init__(f'duplicate key value violates unique constraint "{constraint}"')
        self.constraint_name = constraint
        self.sqlstate = "23505"


async def test_ca08_integrity_error_on_commit_becomes_409_envelope(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CA-8: una violación de integridad ⇒ 409 `integrity_error`, nunca un traceback."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()

    async def failing_commit(session: AsyncSession) -> None:
        raise IntegrityError("INSERT INTO shelves ...", {}, _ConstraintViolation("uq_shelves_name"))

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)

    response = await auth_client.post(SHELVES_PATH, headers=headers, json=shelf_body(tag))
    body = assert_error_envelope(response, 409, "integrity_error")
    assert body["detail"] == "La operación entra en conflicto con el estado actual de los datos"
    assert body["errors"] == []

    usable = await auth_client.get(SHELVES_PATH, headers=headers)
    assert usable.status_code == 200, usable.text
    assert "items" in usable.json()


async def test_catalog_codes_use_the_exact_envelope(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """Cada código del catálogo respeta `{detail, code, errors}` con `errors` lista."""
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    tag = unique_suffix()
    payload = shelf_body(tag)
    created = await auth_client.post(SHELVES_PATH, headers=admin_headers, json=payload)
    assert created.status_code == 201, created.text
    shelf_id = created.json()["id"]
    missing_id = await nonexistent_shelf_id(db_conn)
    inactive_id = await create_auth_user(
        db_conn,
        username=f"sinactivo_{unique_suffix()}",
        role="ADMIN",
        is_active=False,
        password_hash=cached_password_hash(),
    )
    inactive_headers = auth_headers(token_for(inactive_id))

    duplicate = dict(payload)
    duplicate["name"] = str(payload["name"]).upper()

    cases = [
        (await auth_client.get(SHELVES_PATH), 401, "not_authenticated"),
        (
            await auth_client.get(SHELVES_PATH, headers={"Authorization": "Bearer basura"}),
            401,
            "invalid_token",
        ),
        (await auth_client.get(SHELVES_PATH, headers=inactive_headers), 403, "inactive_user"),
        (
            await auth_client.post(
                SHELVES_PATH, headers=medico_headers, json=shelf_body(unique_suffix())
            ),
            403,
            "forbidden",
        ),
        (
            await auth_client.get(shelf_path(missing_id), headers=admin_headers),
            404,
            "shelf_not_found",
        ),
        (
            await auth_client.post(SHELVES_PATH, headers=admin_headers, json=duplicate),
            409,
            "shelf_exists",
        ),
        (
            await auth_client.post(
                SHELVES_PATH, headers=admin_headers, json={**payload, "name": ""}
            ),
            422,
            "validation_error",
        ),
        (
            await auth_client.get(f"{SHELVES_PATH}/abc", headers=admin_headers),
            422,
            "validation_error",
        ),
        (
            await auth_client.put(
                shelf_path(missing_id),
                headers=admin_headers,
                json=shelf_body(unique_suffix()),
            ),
            404,
            "shelf_not_found",
        ),
        (
            await auth_client.delete(shelf_path(missing_id), headers=admin_headers),
            404,
            "shelf_not_found",
        ),
        (
            await auth_client.delete(shelf_path(shelf_id), headers=medico_headers),
            403,
            "forbidden",
        ),
    ]
    for response, status, code in cases:
        body = assert_error_envelope(response, status, code)
        assert isinstance(body["errors"], list)


def test_openapi_declares_the_shelves_contract() -> None:
    """Los cinco endpoints viven bajo `/api/v1/shelves`, con tag, seguridad y códigos."""
    spec = app.openapi()
    paths = spec["paths"]

    shelf_paths = sorted(path for path in paths if path.startswith("/api/v1/shelves"))
    assert shelf_paths == ["/api/v1/shelves", "/api/v1/shelves/{shelf_id}"]

    tags = sorted(
        {
            tag
            for path in shelf_paths
            for method in paths[path].values()
            for tag in method.get("tags", [])
        }
    )
    assert tags == ["shelves"]

    operations = [
        (path, verb, method) for path in shelf_paths for verb, method in paths[path].items()
    ]
    assert len(operations) == 5
    for path, verb, method in operations:
        assert method.get("security"), (path, verb)
        assert method.get("summary"), (path, verb)

    assert "content" in paths["/api/v1/shelves"]["get"]["responses"]["200"]
    assert "content" in paths["/api/v1/shelves"]["post"]["responses"]["201"]

    expected = {
        ("/api/v1/shelves", "get"): ["200", "422"],
        ("/api/v1/shelves", "post"): ["201", "422"],
        ("/api/v1/shelves/{shelf_id}", "get"): ["200", "422"],
        ("/api/v1/shelves/{shelf_id}", "put"): ["200", "422"],
        ("/api/v1/shelves/{shelf_id}", "delete"): ["204", "422"],
    }
    for (path, verb), codes in expected.items():
        assert sorted(paths[path][verb]["responses"]) == codes, (path, verb)

    # Los endpoints siguen existiendo en runtime aunque el contrato no los declare.
    for path in shelf_paths:
        for verb in paths[path]:
            assert paths[path][verb]["tags"] == ["shelves"], (path, verb)


async def test_shelf_with_cells_round_trip_keeps_foreign_keys_consistent(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-1/CA-2: las celdas creadas por la API cuelgan del estante creado."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()

    created = await auth_client.post(
        SHELVES_PATH,
        headers=headers,
        json=shelf_body(tag, cells=shelf_body(tag)["cells"]),
    )
    assert created.status_code == 201, created.text
    shelf_id = created.json()["id"]
    cell_id = created.json()["cells"][0]["id"]

    listed = await auth_client.get(
        SHELVES_PATH, headers=headers, params={"search": f"Estante {tag}", "size": 100}
    )
    item = next(entry for entry in listed.json()["items"] if entry["id"] == shelf_id)
    assert item["cells"][0]["id"] == cell_id
    assert item["cells"][0]["shelf_id"] == shelf_id

    detail = await auth_client.get(shelf_path(shelf_id), headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json() == item

    # Un estante sin celdas se puede crear y leer.
    bare = await create_shelf_with_cells(db_conn, tag=unique_suffix(), positions=())
    assert bare[1] == []
