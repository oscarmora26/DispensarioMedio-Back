"""Listado y detalle de usuarios de FEAT-004 (CA-4 a CA-10, CA-19)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import create_auth_user
from tests.db_support import fetch_scalar, unique_suffix
from tests.users_support import (
    USERS_PATH,
    admin_actor,
    minutes_ago,
    set_last_login_at,
    user_path,
)

ITEM_KEYS = {"id", "username", "email", "role", "doctor_id", "is_active"}
FORBIDDEN_KEYS = {"password", "password_hash", "role_id", "last_login_at", "created_at"}


async def _list(client: AsyncClient, headers: dict[str, str], **params: object) -> dict:
    response = await client.get(USERS_PATH, headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def test_ca04_defaults_and_pagination_formula(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-4: sin parametros `page=1`, `size=20` y `pages = ceil(total/20)`."""
    _, headers = await admin_actor(db_conn)
    for _ in range(3):
        await create_auth_user(db_conn, role="MEDICO")

    total_db = int(await fetch_scalar(db_conn, "SELECT count(*) FROM users"))
    body = await _list(auth_client, headers)

    assert set(body) == {"items", "total", "page", "size", "pages"}
    assert body["page"] == 1
    assert body["size"] == 20
    assert body["total"] == total_db == 4
    assert len(body["items"]) == min(20, total_db)
    assert body["pages"] == (total_db + 19) // 20
    for item in body["items"]:
        assert set(item) == ITEM_KEYS
        assert not FORBIDDEN_KEYS & set(item)


async def test_ca05_page_and_size_limits_and_out_of_range_page(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-5: limites 1..100, 422 con `field` correcto y `page=9999` vacia."""
    _, headers = await admin_actor(db_conn)

    assert (
        await auth_client.get(USERS_PATH, headers=headers, params={"size": 1})
    ).status_code == 200
    assert (
        await auth_client.get(USERS_PATH, headers=headers, params={"size": 100})
    ).status_code == 200

    for params, field in (
        ({"size": 0}, "size"),
        ({"size": 101}, "size"),
        ({"size": -1}, "size"),
        ({"page": 0}, "page"),
        ({"page": -1}, "page"),
    ):
        response = await auth_client.get(USERS_PATH, headers=headers, params=params)
        assert response.status_code == 422, (params, response.text)
        body = response.json()
        assert body["code"] == "validation_error"
        assert {item["field"] for item in body["errors"]} == {field}

    first = await _list(auth_client, headers, page=1)
    far = await _list(auth_client, headers, page=9999)

    assert far["items"] == []
    assert far["page"] == 9999
    assert far["size"] == 20
    assert far["total"] == first["total"]
    assert far["pages"] == first["pages"]


async def test_ca06_sort_catalog_direction_tiebreak_and_nulls(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-6: los cinco campos, ambos sentidos, NULLS LAST, desempate e invalidos."""
    _, headers = await admin_actor(db_conn)
    prefix = f"ord{unique_suffix()}"
    usernames = [f"{prefix}{letter}" for letter in "abcde"]
    emails = [f"{prefix}-{n}@example.test" for n in (5, 4, 3, 2, 1)]
    ids = [
        await create_auth_user(db_conn, username=username, email=email, role="MEDICO")
        for username, email in zip(usernames, emails, strict=True)
    ]

    # Dos marcas de `last_login_at`; el resto queda en NULL.
    late_id, early_id = ids[0], ids[1]
    await set_last_login_at(db_conn, late_id, minutes_ago(1))
    await set_last_login_at(db_conn, early_id, minutes_ago(60))

    def by_field(field: str, desc: bool) -> list[str]:
        items = _list_result[field, desc]
        return [item[field] for item in items]

    _list_result: dict[tuple[str, bool], list[dict]] = {}
    for field in ("username", "email", "created_at", "id"):
        for desc in (False, True):
            body = await _list(
                auth_client,
                headers,
                search=prefix,
                sort=f"-{field}" if desc else field,
                size=100,
            )
            assert body["total"] == 5
            _list_result[field, desc] = body["items"]

    assert by_field("username", False) == usernames
    assert by_field("username", True) == list(reversed(usernames))

    assert by_field("email", False) == sorted(emails)
    assert by_field("email", True) == sorted(emails, reverse=True)

    # Todas las filas comparten `created_at` (now() de la transaccion): desempate id ASC.
    assert [item["id"] for item in _list_result["created_at", False]] == sorted(ids)
    assert [item["id"] for item in _list_result["created_at", True]] == sorted(ids)

    assert by_field("id", False) == sorted(ids)
    assert by_field("id", True) == sorted(ids, reverse=True)

    # `last_login_at`: NULLS LAST en ambos sentidos.
    null_ids = sorted(ids[2:])
    asc = await _list(auth_client, headers, search=prefix, sort="last_login_at", size=100)
    assert [item["id"] for item in asc["items"]][:2] == [early_id, late_id]
    assert sorted(item["id"] for item in asc["items"][2:]) == null_ids

    desc = await _list(auth_client, headers, search=prefix, sort="-last_login_at", size=100)
    assert [item["id"] for item in desc["items"]][:2] == [late_id, early_id]
    assert sorted(item["id"] for item in desc["items"][2:]) == null_ids

    # Sin `sort` el orden es username ASC.
    default = await _list(auth_client, headers, search=prefix, size=100)
    assert [item["username"] for item in default["items"]] == usernames

    for invalid in ("password_hash", "username,email", ""):
        response = await auth_client.get(USERS_PATH, headers=headers, params={"sort": invalid})
        assert response.status_code == 422, invalid
        body = response.json()
        assert body["code"] == "validation_error"
        assert {item["field"] for item in body["errors"]} == {"sort"}


async def test_ca07_search_is_case_insensitive_partial_and_escapes_wildcards(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-7: busqueda case-insensitive, comodines escapados y longitud maxima."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    percent = f"ab%cd{tag}"
    plain = f"abcd{tag}"
    underscore = f"x_y{tag}"
    plain_underscore = f"xy{tag}"
    slash = f"bar\\baz{tag}"
    mixed = f"Mixed{tag}"
    mail_username = f"mail{tag}"
    mail_email = f"correo.{tag}@example.test"

    await create_auth_user(db_conn, username=percent, email=f"p{tag}@example.test", role="MEDICO")
    await create_auth_user(db_conn, username=plain, email=f"q{tag}@example.test", role="MEDICO")
    await create_auth_user(
        db_conn, username=underscore, email=f"u{tag}@example.test", role="MEDICO"
    )
    await create_auth_user(
        db_conn, username=plain_underscore, email=f"v{tag}@example.test", role="MEDICO"
    )
    await create_auth_user(db_conn, username=slash, email=f"s{tag}@example.test", role="MEDICO")
    await create_auth_user(db_conn, username=mixed, email=f"m{tag}@example.test", role="MEDICO")
    await create_auth_user(db_conn, username=mail_username, email=mail_email, role="MEDICO")

    no_filter = await _list(auth_client, headers, size=100)

    case_insensitive = await _list(auth_client, headers, search=f"MIXED{tag.upper()}", size=100)
    assert [item["username"] for item in case_insensitive["items"]] == [mixed]

    partial_email = await _list(auth_client, headers, search=f"correo.{tag}", size=100)
    assert [item["username"] for item in partial_email["items"]] == [mail_username]

    percent_only = await _list(auth_client, headers, search="%", size=100)
    assert [item["username"] for item in percent_only["items"]] == [percent]
    assert percent_only["total"] == 1

    underscore_only = await _list(auth_client, headers, search="_", size=100)
    assert [item["username"] for item in underscore_only["items"]] == [underscore]
    assert underscore_only["total"] == 1

    slash_only = await _list(auth_client, headers, search="\\", size=100)
    assert [item["username"] for item in slash_only["items"]] == [slash]

    whitespace = await _list(auth_client, headers, search="   ", size=100)
    assert whitespace["total"] == no_filter["total"]
    assert whitespace["pages"] == no_filter["pages"]

    too_long = await auth_client.get(USERS_PATH, headers=headers, params={"search": "x" * 101})
    assert too_long.status_code == 422
    assert {item["field"] for item in too_long.json()["errors"]} == {"search"}


async def test_ca08_filter_by_is_active_and_combination_with_search(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-8: `is_active` solo, ausente y combinado con `search`."""
    _, headers = await admin_actor(db_conn)
    prefix = f"act{unique_suffix()}"
    active_id = await create_auth_user(
        db_conn, username=f"{prefix}_on", email=f"{prefix}_on@example.test", role="MEDICO"
    )
    inactive_id = await create_auth_user(
        db_conn,
        username=f"{prefix}_off",
        email=f"{prefix}_off@example.test",
        role="MEDICO",
        is_active=False,
    )
    other_id = await create_auth_user(db_conn, username=f"other_{unique_suffix()}", role="MEDICO")

    both = await _list(auth_client, headers, search=prefix, size=100)
    assert {item["id"] for item in both["items"]} == {active_id, inactive_id}
    assert other_id not in {item["id"] for item in both["items"]}

    only_active = await _list(auth_client, headers, search=prefix, is_active="true", size=100)
    assert {item["id"] for item in only_active["items"]} == {active_id}
    assert all(item["is_active"] is True for item in only_active["items"])

    only_inactive = await _list(auth_client, headers, search=prefix, is_active="false", size=100)
    assert {item["id"] for item in only_inactive["items"]} == {inactive_id}
    assert all(item["is_active"] is False for item in only_inactive["items"])

    unfiltered_active = await _list(auth_client, headers, is_active="true", size=100)
    assert other_id in {item["id"] for item in unfiltered_active["items"]}


async def test_ca09_search_without_matches_reports_zero_pages(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-9: sin coincidencias `total=0`, `pages=0` y `page`/`size` pedidos."""
    _, headers = await admin_actor(db_conn)

    body = await _list(auth_client, headers, search=f"nada_{unique_suffix()}", page=2, size=5)

    assert body["items"] == []
    assert body["total"] == 0
    assert body["pages"] == 0
    assert body["page"] == 2
    assert body["size"] == 5


async def test_ca10_item_shape_is_exact_in_list(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-10: cada `item` tiene exactamente las seis claves de D-4."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    username = f"shape_{tag}"
    await create_auth_user(
        db_conn, username=username, email=f"shape_{tag}@example.test", role="MEDICO"
    )

    item = (await _list(auth_client, headers, search=username))["items"][0]

    assert set(item) == ITEM_KEYS
    assert not FORBIDDEN_KEYS & set(item)
    assert item["username"] == username


async def test_ca19_detail_matches_list_and_supports_inactive_users(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-19: detalle 200 (activo e inactivo), misma forma y 422 de `user_id`."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    username = f"detail_{tag}"
    user_id = await create_auth_user(
        db_conn, username=username, email=f"detail_{tag}@example.test", role="MEDICO"
    )
    inactive_id = await create_auth_user(
        db_conn,
        username=f"inactive_{tag}",
        email=f"inactive_{tag}@example.test",
        role="MEDICO",
        is_active=False,
    )

    list_item = (await _list(auth_client, headers, search=username))["items"][0]
    detail = await auth_client.get(user_path(user_id), headers=headers)

    assert detail.status_code == 200
    assert detail.json() == list_item
    assert set(detail.json()) == ITEM_KEYS
    assert not FORBIDDEN_KEYS & set(detail.json())

    inactive = await auth_client.get(user_path(inactive_id), headers=headers)
    assert inactive.status_code == 200
    assert inactive.json()["is_active"] is False

    invalid = await auth_client.get(f"{USERS_PATH}/abc", headers=headers)
    assert invalid.status_code == 422
    body = invalid.json()
    assert body["code"] == "validation_error"
    assert {item["field"] for item in body["errors"]} == {"user_id"}
