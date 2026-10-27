"""FEAT-018: el recurso medico expone su asociacion con un usuario (solo lectura)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import create_auth_user
from tests.db_support import unique_suffix
from tests.doctors_support import DOCTORS_PATH, admin_actor, create_doctor_row

#: Claves exactas del usuario anidado; el email y el hash nunca se exponen.
USER_KEYS = {"id", "username", "is_active"}


async def _detail(client: AsyncClient, headers: dict[str, str], doctor_id: int) -> dict:
    response = await client.get(f"{DOCTORS_PATH}/{doctor_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def test_doctor_without_user_exposes_null(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """Un medico sin cuenta responde `user: null` (regla 24 de FEAT-012)."""
    _, headers = await admin_actor(db_conn)
    doctor_id = await create_doctor_row(db_conn)

    body = await _detail(auth_client, headers, doctor_id)

    assert "user" in body
    assert body["user"] is None


async def test_doctor_with_user_exposes_id_username_and_is_active(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """Un medico asociado expone exactamente `id`, `username` e `is_active`."""
    _, headers = await admin_actor(db_conn)
    doctor_id = await create_doctor_row(db_conn)
    tag = unique_suffix()
    user_id = await create_auth_user(
        db_conn, username=f"medico.{tag}", role="MEDICO", doctor_id=doctor_id
    )

    body = await _detail(auth_client, headers, doctor_id)

    assert body["user"] is not None
    assert set(body["user"]) == USER_KEYS
    assert body["user"]["id"] == user_id
    assert body["user"]["username"] == f"medico.{tag}"
    assert body["user"]["is_active"] is True


async def test_nested_user_never_exposes_email_or_password_hash(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """El usuario anidado es un resumen: sin email, sin hash, sin rol."""
    _, headers = await admin_actor(db_conn)
    doctor_id = await create_doctor_row(db_conn)
    await create_auth_user(db_conn, role="MEDICO", doctor_id=doctor_id)

    body = await _detail(auth_client, headers, doctor_id)

    assert set(body["user"]) == USER_KEYS
    assert "email" not in body["user"]
    assert "password_hash" not in body["user"]
    assert "role" not in body["user"]


async def test_listing_includes_nested_user_for_every_item(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """El listado expone la asociacion en cada item, incluidos los `null`."""
    _, headers = await admin_actor(db_conn)
    with_user = await create_doctor_row(db_conn)
    await create_auth_user(db_conn, role="MEDICO", doctor_id=with_user)

    response = await auth_client.get(DOCTORS_PATH, headers=headers, params={"size": 100})
    assert response.status_code == 200, response.text
    items = {item["id"]: item for item in response.json()["items"]}

    assert items[with_user]["user"] is not None
    assert set(items[with_user]["user"]) == USER_KEYS
    assert all("user" in item for item in items.values())


async def test_medico_sees_his_own_nested_user(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """El medico ve su propio resumen; el filtro de propiedad sigue aplicando."""
    doctor_id = await create_doctor_row(db_conn)
    user_id = await create_auth_user(db_conn, role="MEDICO", doctor_id=doctor_id)
    token = await _login(auth_client, db_conn, user_id)
    headers = {"Authorization": f"Bearer {token}"}

    body = await _detail(auth_client, headers, doctor_id)

    assert body["user"] is not None
    assert body["user"]["id"] == user_id


async def test_medico_still_cannot_read_another_doctor(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """La asociacion no relaja D-6: otro medico sigue siendo `404`."""
    own = await create_doctor_row(db_conn)
    other = await create_doctor_row(db_conn)
    user_id = await create_auth_user(db_conn, role="MEDICO", doctor_id=own)
    token = await _login(auth_client, db_conn, user_id)
    headers = {"Authorization": f"Bearer {token}"}

    response = await auth_client.get(f"{DOCTORS_PATH}/{other}", headers=headers)

    assert response.status_code == 404
    assert response.json()["code"] == "doctor_not_found"


async def _login(client: AsyncClient, conn: AsyncConnection, user_id: int) -> str:
    """Autentica al usuario recien creado y devuelve el token."""
    from sqlalchemy import text

    result = await conn.execute(text("SELECT username FROM users WHERE id = :id"), {"id": user_id})
    username = result.scalar_one()
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "ClaveDePrueba123"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]
