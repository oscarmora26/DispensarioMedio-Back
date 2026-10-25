"""Perfil del medico de FEAT-012 (CA-5 a CA-8, US-28)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.doctors_support import (
    DOCTORS_PATH,
    admin_actor,
    assert_error_envelope,
    create_doctor_row,
    doctor_path,
    medico_actor,
    set_doctor_active,
    unique_suffix,
)


async def test_ca5_medico_list_shows_only_own_profile(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-5: `MEDICO` ve solo su propio registro en el listado; `ADMIN` ve todos."""
    own = await create_doctor_row(db_conn, name=f"Propio {unique_suffix()}")
    await create_doctor_row(db_conn, name=f"Ajeno {unique_suffix()}")

    _, medico_headers = await medico_actor(db_conn, doctor_id=own)
    mine = await auth_client.get(DOCTORS_PATH, headers=medico_headers)
    assert mine.status_code == 200, mine.text
    body = mine.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [own]

    _, admin_headers = await admin_actor(db_conn)
    everyone = await auth_client.get(DOCTORS_PATH, headers=admin_headers)
    assert everyone.status_code == 200, everyone.text
    assert everyone.json()["total"] >= 2


async def test_ca6_medico_without_association_gets_empty_page(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-6: `MEDICO` sin `doctor_id` ⇒ pagina vacia con `total == 0`."""
    await create_doctor_row(db_conn)

    _, medico_headers = await medico_actor(db_conn)
    response = await auth_client.get(DOCTORS_PATH, headers=medico_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["pages"] == 0


async def test_ca7_medico_detail_is_own_only_and_foreign_is_404(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-7: detalle propio ⇒ 200; detalle ajeno (exista o no) ⇒ 404."""
    own = await create_doctor_row(db_conn, name=f"Propio {unique_suffix()}")
    foreign = await create_doctor_row(db_conn, name=f"Ajeno {unique_suffix()}")

    _, medico_headers = await medico_actor(db_conn, doctor_id=own)
    mine = await auth_client.get(doctor_path(own), headers=medico_headers)
    assert mine.status_code == 200, mine.text

    other = await auth_client.get(doctor_path(foreign), headers=medico_headers)
    assert_error_envelope(other, 404, "doctor_not_found")

    _, admin_headers = await admin_actor(db_conn)
    as_admin = await auth_client.get(doctor_path(foreign), headers=admin_headers)
    assert as_admin.status_code == 200, as_admin.text


async def test_ca8_medico_filters_combine_with_ownership(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-8: `search`/`is_active`/`sort` se combinan en `AND` con el filtro propio."""
    own = await create_doctor_row(db_conn, name=f"Unico {unique_suffix()}")
    await create_doctor_row(db_conn, name=f"Unico Ajeno {unique_suffix()}")
    await create_doctor_row(db_conn, name=f"Otro {unique_suffix()}")

    _, medico_headers = await medico_actor(db_conn, doctor_id=own)

    matches_own = await auth_client.get(
        DOCTORS_PATH, headers=medico_headers, params={"search": "Unico"}
    )
    assert matches_own.status_code == 200, matches_own.text
    assert [item["id"] for item in matches_own.json()["items"]] == [own]

    misses_own = await auth_client.get(
        DOCTORS_PATH, headers=medico_headers, params={"search": "Otro"}
    )
    assert misses_own.status_code == 200, misses_own.text
    assert misses_own.json()["total"] == 0

    await set_doctor_active(db_conn, own, False)
    inactive = await auth_client.get(
        DOCTORS_PATH, headers=medico_headers, params={"is_active": "false"}
    )
    assert [item["id"] for item in inactive.json()["items"]] == [own]

    sorted_list = await auth_client.get(
        DOCTORS_PATH, headers=medico_headers, params={"sort": "-id"}
    )
    assert sorted_list.status_code == 200, sorted_list.text
    assert [item["id"] for item in sorted_list.json()["items"]] == [own]
