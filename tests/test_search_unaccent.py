"""FEAT-016: busqueda insensible a acentos en los listados con `search` (CA-1 a CA-7)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests import (
    brands_support,
    doctors_support,
    drug_types_support,
    locations_support,
    medications_support,
    patients_support,
    users_support,
)
from tests.auth_support import create_auth_user
from tests.db_support import unique_suffix


async def _search(client: AsyncClient, headers: dict[str, str], path: str, term: str) -> dict:
    response = await client.get(path, headers=headers, params={"search": term})
    assert response.status_code == 200, response.text
    return response.json()


async def test_ca01_drug_types_search_ignores_accents_both_directions(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-1: `capsula` encuentra `Cápsula` y `Inyéctable` encuentra `Inyectable`."""
    _, headers = await drug_types_support.admin_actor(db_conn)
    tag = unique_suffix()
    accented_id = await drug_types_support.create_drug_type_row(db_conn, f"Cápsula {tag}")
    plain_id = await drug_types_support.create_drug_type_row(db_conn, f"Inyectable {tag}")

    body = await _search(auth_client, headers, drug_types_support.DRUG_TYPES_PATH, f"capsula {tag}")
    assert body["total"] == 1
    assert body["items"][0]["id"] == accented_id

    body = await _search(
        auth_client, headers, drug_types_support.DRUG_TYPES_PATH, f"Inyéctable {tag}"
    )
    assert body["total"] == 1
    assert body["items"][0]["id"] == plain_id


async def test_ca02_brands_search_ignores_accents(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-2: `generico` encuentra `Genérico`."""
    _, headers = await brands_support.admin_actor(db_conn)
    tag = unique_suffix()
    brand_id = await brands_support.create_brand_row(db_conn, f"Genérico {tag}")

    body = await _search(auth_client, headers, brands_support.BRANDS_PATH, f"generico {tag}")
    assert body["total"] == 1
    assert body["items"][0]["id"] == brand_id


async def test_ca03_locations_search_ignores_accents(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-3: `deposito` encuentra `Depósito`."""
    _, headers = await locations_support.admin_actor(db_conn)
    tag = unique_suffix()
    location_id = await locations_support.create_location_row(db_conn, f"Depósito {tag}")

    body = await _search(auth_client, headers, locations_support.LOCATIONS_PATH, f"deposito {tag}")
    assert body["total"] == 1
    assert body["items"][0]["id"] == location_id


async def test_ca04_medications_search_ignores_accents(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-4: `acido` encuentra `Ácido`."""
    _, headers = await medications_support.admin_actor(db_conn)
    tag = unique_suffix()
    medication_id = await medications_support.create_medication_row(
        db_conn, description=f"Ácido {tag}"
    )

    body = await _search(auth_client, headers, medications_support.MEDICATIONS_PATH, f"acido {tag}")
    assert body["total"] == 1
    assert body["items"][0]["id"] == medication_id


async def test_ca05_patients_search_ignores_accents(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-5: `Jose` encuentra `José`."""
    _, headers = await patients_support.admin_actor(db_conn)
    tag = unique_suffix()
    patient_id = await patients_support.create_patient_row(db_conn, name=f"José {tag}")

    body = await _search(auth_client, headers, patients_support.PATIENTS_PATH, f"Jose {tag}")
    assert body["total"] == 1
    assert body["items"][0]["id"] == patient_id


async def test_ca06_doctors_search_ignores_accents(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-6: `Maria` encuentra `María`."""
    _, headers = await doctors_support.admin_actor(db_conn)
    tag = unique_suffix()
    doctor_id = await doctors_support.create_doctor_row(db_conn, name=f"Dra. María {tag}")

    body = await _search(auth_client, headers, doctors_support.DOCTORS_PATH, f"Maria {tag}")
    assert body["total"] == 1
    assert body["items"][0]["id"] == doctor_id


async def test_ca07_users_search_ignores_accents_in_term(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-7: el termino acentuado `josé` encuentra el username sin acento `jose`."""
    _, headers = await users_support.admin_actor(db_conn)
    tag = unique_suffix()
    user_id = await create_auth_user(db_conn, username=f"jose.{tag}")

    body = await _search(auth_client, headers, users_support.USERS_PATH, f"josé.{tag}")
    assert body["total"] == 1
    assert body["items"][0]["id"] == user_id
