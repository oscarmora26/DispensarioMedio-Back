"""Autorizacion y contrato de errores de celdas (FEAT-017, R-4)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import auth_headers, cached_password_hash, create_auth_user, token_for
from tests.db_support import unique_suffix
from tests.shelves_support import (
    CELL_KEYS,
    CELL_NOT_FOUND_DETAIL,
    CELLS_PATH,
    admin_actor,
    assert_error_envelope,
    cell_body,
    cell_path,
    count_cells,
    create_cell,
    create_shelf_with_cells,
    fetch_cell_row,
    medico_actor,
    nonexistent_cell_id,
)


async def test_r4_medico_reads_but_never_writes(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """R-4: MEDICO lee (200) y recibe 403 `forbidden` al escribir, sin efectos."""
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    tag = unique_suffix()
    shelf_id, cell_ids = await create_shelf_with_cells(
        db_conn, tag=tag, positions=(("A", "1"), ("A", "2"))
    )
    cell_id = cell_ids[0]
    target = await fetch_cell_row(db_conn, cell_id)
    assert target is not None
    before_count = await count_cells(db_conn)

    listed = await auth_client.get(CELLS_PATH, headers=medico_headers)
    assert listed.status_code == 200, listed.text
    assert "items" in listed.json()

    detail = await auth_client.get(cell_path(cell_id), headers=medico_headers)
    assert detail.status_code == 200, detail.text
    assert set(detail.json()) == CELL_KEYS

    responses = [
        await auth_client.post(
            CELLS_PATH, headers=medico_headers, json=cell_body(unique_suffix(), shelf_id=shelf_id)
        ),
        await auth_client.put(
            cell_path(cell_id),
            headers=medico_headers,
            json={
                "shelf_id": shelf_id,
                "section": str(target["section"]),
                "cell": str(target["cell"]),
                "description": f"Intento {tag}",
                "is_active": False,
            },
        ),
        await auth_client.delete(cell_path(cell_id), headers=medico_headers),
    ]
    for response in responses:
        body = assert_error_envelope(response, 403, "forbidden")
        assert body["errors"] == []

    after = await fetch_cell_row(db_conn, cell_id)
    assert after is not None
    assert after == target
    assert await count_cells(db_conn) == before_count

    # El ADMIN sí puede escribir sobre los mismos recursos.
    created = await create_cell(auth_client, admin_headers, shelf_id=shelf_id, tag=unique_suffix())
    assert created.status_code == 201, created.text


async def test_r4_authentication_matrix(auth_client: AsyncClient, db_conn: AsyncConnection) -> None:
    """R-4: 401 sin cabecera/esquema ajeno/token inválido, 403 de inactivos."""
    admin_id, _ = await admin_actor(db_conn)

    missing = await auth_client.get(CELLS_PATH)
    body = assert_error_envelope(missing, 401, "not_authenticated")
    assert missing.headers["www-authenticate"].lower().startswith("bearer")
    assert body["errors"] == []

    basic = await auth_client.get(CELLS_PATH, headers={"Authorization": "Basic abc"})
    assert_error_envelope(basic, 401, "not_authenticated")
    assert basic.headers["www-authenticate"].lower().startswith("bearer")

    garbage = await auth_client.get(CELLS_PATH, headers={"Authorization": "Bearer basura"})
    assert_error_envelope(garbage, 401, "invalid_token")

    foreign = await auth_client.get(
        CELLS_PATH,
        headers=auth_headers(token_for(admin_id, secret="otro-secreto-de-prueba-1234567890")),
    )
    assert_error_envelope(foreign, 401, "invalid_token")

    unknown = await auth_client.get(CELLS_PATH, headers=auth_headers(token_for(999_999_999)))
    assert_error_envelope(unknown, 401, "invalid_token")

    expired = await auth_client.get(
        CELLS_PATH, headers=auth_headers(token_for(admin_id, expires_minutes=-1))
    )
    assert_error_envelope(expired, 401, "invalid_token")

    shelf_id, _ = await create_shelf_with_cells(db_conn, tag=unique_suffix(), positions=())
    for role in ("ADMIN", "MEDICO"):
        inactive_id = await create_auth_user(
            db_conn,
            username=f"sinactivo_{unique_suffix()}",
            role=role,
            is_active=False,
            password_hash=cached_password_hash(),
        )
        inactive_headers = auth_headers(token_for(inactive_id, role=role))
        list_response = await auth_client.get(CELLS_PATH, headers=inactive_headers)
        assert_error_envelope(list_response, 403, "inactive_user")
        post_response = await auth_client.post(
            CELLS_PATH,
            headers=inactive_headers,
            json=cell_body(unique_suffix(), shelf_id=shelf_id),
        )
        assert_error_envelope(post_response, 403, "inactive_user")


async def test_detail_of_missing_cell_is_404_with_exact_envelope(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """Un `GET /{id}` inexistente ⇒ 404 `cell_not_found` con el envelope exacto."""
    _, headers = await admin_actor(db_conn)
    missing_id = await nonexistent_cell_id(db_conn)

    response = await auth_client.get(cell_path(missing_id), headers=headers)
    body = assert_error_envelope(response, 404, "cell_not_found")
    assert body["detail"] == CELL_NOT_FOUND_DETAIL
    assert body["errors"] == []

    bad = await auth_client.get(f"{CELLS_PATH}/abc", headers=headers)
    assert bad.status_code == 422, bad.text
    assert {item["field"] for item in bad.json()["errors"]} == {"cell_id"}
