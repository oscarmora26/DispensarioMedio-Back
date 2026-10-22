"""Autorización y contrato de errores de FEAT-010 (CA-1, CA-2, CA-3)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import auth_headers, create_auth_user, token_for
from tests.db_support import unique_suffix
from tests.medications_support import (
    MEDICATIONS_PATH,
    admin_actor,
    assert_error_envelope,
    count_medications,
    create_medication_row,
    fetch_medication_row,
    make_catalogs,
    medication_body,
    medication_path,
    medico_actor,
    nonexistent_medication_id,
)


async def test_ca01_medico_reads_but_never_writes(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-1: MEDICO lee (200) y recibe 403 `forbidden` al escribir, sin efectos."""
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    drug_type_id, brand_id, location_id = await make_catalogs(db_conn)
    tag = unique_suffix()
    active_id = await create_medication_row(
        db_conn,
        f"Activa {tag}",
        drug_type_id=drug_type_id,
        brand_id=brand_id,
        location_id=location_id,
        is_active=True,
    )
    target = await fetch_medication_row(db_conn, active_id)
    assert target is not None
    before_count = await count_medications(db_conn)
    write_tag = unique_suffix()

    listed = await auth_client.get(MEDICATIONS_PATH, headers=medico_headers)
    assert listed.status_code == 200, listed.text

    detail = await auth_client.get(medication_path(active_id), headers=medico_headers)
    assert detail.status_code == 200, detail.text

    write_body = medication_body(write_tag, drug_type_id, brand_id, location_id)
    responses = [
        await auth_client.post(MEDICATIONS_PATH, headers=medico_headers, json=write_body),
        await auth_client.put(medication_path(active_id), headers=medico_headers, json=write_body),
        await auth_client.delete(medication_path(active_id), headers=medico_headers),
    ]
    for response in responses:
        body = assert_error_envelope(response, 403, "forbidden")
        assert body["errors"] == []

    after = await fetch_medication_row(db_conn, active_id)
    assert after is not None
    assert after["description"] == target["description"]
    assert after["dose"] == target["dose"]
    assert after["drug_type_id"] == target["drug_type_id"]
    assert after["brand_id"] == target["brand_id"]
    assert after["location_id"] == target["location_id"]
    assert after["is_active"] == target["is_active"]
    assert await count_medications(db_conn) == before_count

    created = await auth_client.post(MEDICATIONS_PATH, headers=admin_headers, json=write_body)
    assert created.status_code == 201, created.text


async def test_ca02_authentication_matrix(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-2: 401 sin cabecera/esquema ajeno/token inválido, 403 de inactivos."""
    admin_id, _ = await admin_actor(db_conn)
    drug_type_id, brand_id, location_id = await make_catalogs(db_conn)

    missing = await auth_client.get(MEDICATIONS_PATH)
    body = assert_error_envelope(missing, 401, "not_authenticated")
    assert missing.headers["www-authenticate"].lower().startswith("bearer")
    assert body["errors"] == []

    basic = await auth_client.get(MEDICATIONS_PATH, headers={"Authorization": "Basic abc"})
    assert_error_envelope(basic, 401, "not_authenticated")
    assert basic.headers["www-authenticate"].lower().startswith("bearer")

    garbage = await auth_client.get(MEDICATIONS_PATH, headers={"Authorization": "Bearer basura"})
    assert_error_envelope(garbage, 401, "invalid_token")

    foreign = await auth_client.get(
        MEDICATIONS_PATH,
        headers=auth_headers(token_for(admin_id, secret="otro-secreto-de-prueba-1234567890")),
    )
    assert_error_envelope(foreign, 401, "invalid_token")

    unknown = await auth_client.get(MEDICATIONS_PATH, headers=auth_headers(token_for(999_999_999)))
    assert_error_envelope(unknown, 401, "invalid_token")

    expired = await auth_client.get(
        MEDICATIONS_PATH, headers=auth_headers(token_for(admin_id, expires_minutes=-1))
    )
    assert_error_envelope(expired, 401, "invalid_token")

    for role in ("ADMIN", "MEDICO"):
        suffix = unique_suffix()
        inactive_id = await create_auth_user(
            db_conn,
            username=f"minactivo_{suffix}",
            email=f"minactivo_{suffix}@example.test",
            role=role,
            is_active=False,
        )
        inactive_headers = auth_headers(token_for(inactive_id, role=role))
        list_response = await auth_client.get(MEDICATIONS_PATH, headers=inactive_headers)
        assert_error_envelope(list_response, 403, "inactive_user")
        post_response = await auth_client.post(
            MEDICATIONS_PATH,
            headers=inactive_headers,
            json=medication_body(unique_suffix(), drug_type_id, brand_id, location_id),
        )
        assert_error_envelope(post_response, 403, "inactive_user")


async def test_ca03_detail_of_missing_id_is_404_with_exact_envelope(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-3: `GET /{id}` inexistente ⇒ 404 `medication_not_found` en español."""
    _, headers = await admin_actor(db_conn)
    missing_id = await nonexistent_medication_id(db_conn)

    response = await auth_client.get(medication_path(missing_id), headers=headers)
    body = assert_error_envelope(response, 404, "medication_not_found")
    assert body["detail"] == "El medicamento no existe"
    assert body["errors"] == []
    assert "details" not in response.json()
