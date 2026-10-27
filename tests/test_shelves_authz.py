"""Autorización y contrato de errores de FEAT-017 (R-4)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import auth_headers, cached_password_hash, create_auth_user, token_for
from tests.db_support import unique_suffix
from tests.shelves_support import (
    CELL_KEYS,
    ITEM_KEYS,
    SHELF_NOT_FOUND_DETAIL,
    SHELVES_PATH,
    admin_actor,
    assert_error_envelope,
    count_shelves,
    create_shelf_with_cells,
    fetch_shelf_row,
    medico_actor,
    nonexistent_shelf_id,
    shelf_body,
    shelf_path,
)


async def test_r4_medico_reads_but_never_writes(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """R-4: MEDICO lee (200) y recibe 403 `forbidden` al escribir, sin efectos."""
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    tag = unique_suffix()
    active_id, _ = await create_shelf_with_cells(db_conn, tag=tag, is_active=True)
    inactive_id, _ = await create_shelf_with_cells(db_conn, tag=unique_suffix(), is_active=False)
    target = await fetch_shelf_row(db_conn, active_id)
    assert target is not None
    before_count = await count_shelves(db_conn)
    write_tag = unique_suffix()

    listed = await auth_client.get(SHELVES_PATH, headers=medico_headers)
    assert listed.status_code == 200, listed.text

    detail = await auth_client.get(shelf_path(active_id), headers=medico_headers)
    assert detail.status_code == 200, detail.text
    assert set(detail.json()) == ITEM_KEYS
    for cell in detail.json()["cells"]:
        assert set(cell) == CELL_KEYS

    responses = [
        await auth_client.post(SHELVES_PATH, headers=medico_headers, json=shelf_body(write_tag)),
        await auth_client.put(
            shelf_path(active_id),
            headers=medico_headers,
            json=shelf_body(write_tag, is_active=True),
        ),
        await auth_client.delete(shelf_path(active_id), headers=medico_headers),
    ]
    for response in responses:
        body = assert_error_envelope(response, 403, "forbidden")
        assert body["errors"] == []

    after = await fetch_shelf_row(db_conn, active_id)
    assert after is not None
    assert after["name"] == target["name"]
    assert after["description"] == target["description"]
    assert after["is_active"] == target["is_active"]
    assert await count_shelves(db_conn) == before_count

    # El ADMIN sí puede escribir sobre los mismos recursos.
    created = await auth_client.post(
        SHELVES_PATH, headers=admin_headers, json=shelf_body(write_tag)
    )
    assert created.status_code == 201, created.text
    medico_inactive = await auth_client.get(shelf_path(inactive_id), headers=medico_headers)
    assert medico_inactive.status_code == 200


async def test_r4_authentication_matrix(auth_client: AsyncClient, db_conn: AsyncConnection) -> None:
    """R-4: 401 sin cabecera/esquema ajeno/token inválido, 403 de inactivos."""
    admin_id, _ = await admin_actor(db_conn)

    missing = await auth_client.get(SHELVES_PATH)
    body = assert_error_envelope(missing, 401, "not_authenticated")
    assert missing.headers["www-authenticate"].lower().startswith("bearer")
    assert body["errors"] == []

    basic = await auth_client.get(SHELVES_PATH, headers={"Authorization": "Basic abc"})
    assert_error_envelope(basic, 401, "not_authenticated")
    assert basic.headers["www-authenticate"].lower().startswith("bearer")

    garbage = await auth_client.get(SHELVES_PATH, headers={"Authorization": "Bearer basura"})
    assert_error_envelope(garbage, 401, "invalid_token")

    foreign = await auth_client.get(
        SHELVES_PATH,
        headers=auth_headers(token_for(admin_id, secret="otro-secreto-de-prueba-1234567890")),
    )
    assert_error_envelope(foreign, 401, "invalid_token")

    unknown = await auth_client.get(SHELVES_PATH, headers=auth_headers(token_for(999_999_999)))
    assert_error_envelope(unknown, 401, "invalid_token")

    expired = await auth_client.get(
        SHELVES_PATH, headers=auth_headers(token_for(admin_id, expires_minutes=-1))
    )
    assert_error_envelope(expired, 401, "invalid_token")

    for role in ("ADMIN", "MEDICO"):
        inactive_id = await create_auth_user(
            db_conn,
            username=f"sinactivo_{unique_suffix()}",
            role=role,
            is_active=False,
            password_hash=cached_password_hash(),
        )
        inactive_headers = auth_headers(token_for(inactive_id, role=role))
        list_response = await auth_client.get(SHELVES_PATH, headers=inactive_headers)
        assert_error_envelope(list_response, 403, "inactive_user")
        post_response = await auth_client.post(
            SHELVES_PATH, headers=inactive_headers, json=shelf_body(unique_suffix())
        )
        assert_error_envelope(post_response, 403, "inactive_user")


async def test_detail_of_missing_shelf_is_404_with_exact_envelope(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """Un `GET /{id}` inexistente ⇒ 404 `shelf_not_found` con el envelope exacto."""
    _, headers = await admin_actor(db_conn)
    missing_id = await nonexistent_shelf_id(db_conn)

    response = await auth_client.get(shelf_path(missing_id), headers=headers)
    body = assert_error_envelope(response, 404, "shelf_not_found")
    assert body["detail"] == SHELF_NOT_FOUND_DETAIL
    assert body["errors"] == []
