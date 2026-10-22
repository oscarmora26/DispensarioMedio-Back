"""Autorizacion y contrato de errores de FEAT-007 (CA-1, CA-2, CA-3)."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.auth_support import auth_headers, cached_password_hash, create_auth_user, token_for
from tests.db_support import unique_suffix
from tests.drug_types_support import (
    DRUG_TYPES_PATH,
    admin_actor,
    assert_error_envelope,
    count_drug_types,
    create_drug_type_row,
    drug_type_path,
    fetch_drug_type_row,
    medico_actor,
    nonexistent_drug_type_id,
)


async def test_ca01_medico_reads_but_never_writes(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-1: MEDICO lee (200) y recibe 403 `forbidden` al escribir, sin efectos."""
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    active_id = await create_drug_type_row(db_conn, f"Activo {unique_suffix()}", is_active=True)
    inactive_id = await create_drug_type_row(
        db_conn, f"Inactivo {unique_suffix()}", is_active=False
    )
    target = await fetch_drug_type_row(db_conn, active_id)
    assert target is not None
    before_count = await count_drug_types(db_conn)
    tag = unique_suffix()

    listed = await auth_client.get(DRUG_TYPES_PATH, headers=medico_headers)
    assert listed.status_code == 200, listed.text

    detail = await auth_client.get(drug_type_path(active_id), headers=medico_headers)
    assert detail.status_code == 200, detail.text

    responses = [
        await auth_client.post(
            DRUG_TYPES_PATH,
            headers=medico_headers,
            json={"description": f"Nuevo {tag}", "is_active": True},
        ),
        await auth_client.put(
            drug_type_path(active_id),
            headers=medico_headers,
            json={"description": f"Cambiado {tag}", "is_active": True},
        ),
        await auth_client.delete(drug_type_path(active_id), headers=medico_headers),
    ]
    for response in responses:
        body = assert_error_envelope(response, 403, "forbidden")
        assert body["errors"] == []

    after = await fetch_drug_type_row(db_conn, active_id)
    assert after is not None
    assert after["description"] == target["description"]
    assert after["is_active"] == target["is_active"]
    assert await count_drug_types(db_conn) == before_count

    # El ADMIN si puede escribir sobre los mismos recursos.
    created = await auth_client.post(
        DRUG_TYPES_PATH,
        headers=admin_headers,
        json={"description": f"Nuevo {tag}", "is_active": True},
    )
    assert created.status_code == 201, created.text
    medico_inactive = await auth_client.get(drug_type_path(inactive_id), headers=medico_headers)
    assert medico_inactive.status_code == 200


async def test_ca02_authentication_matrix(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-2: 401 sin cabecera/esquema ajeno/token invalido, 403 de inactivos."""
    admin_id, _ = await admin_actor(db_conn)

    missing = await auth_client.get(DRUG_TYPES_PATH)
    body = assert_error_envelope(missing, 401, "not_authenticated")
    assert missing.headers["www-authenticate"].lower().startswith("bearer")
    assert body["errors"] == []

    basic = await auth_client.get(DRUG_TYPES_PATH, headers={"Authorization": "Basic abc"})
    assert_error_envelope(basic, 401, "not_authenticated")
    assert basic.headers["www-authenticate"].lower().startswith("bearer")

    garbage = await auth_client.get(DRUG_TYPES_PATH, headers={"Authorization": "Bearer basura"})
    assert_error_envelope(garbage, 401, "invalid_token")

    foreign = await auth_client.get(
        DRUG_TYPES_PATH,
        headers=auth_headers(token_for(admin_id, secret="otro-secreto-de-prueba-1234567890")),
    )
    assert_error_envelope(foreign, 401, "invalid_token")

    unknown = await auth_client.get(DRUG_TYPES_PATH, headers=auth_headers(token_for(999_999_999)))
    assert_error_envelope(unknown, 401, "invalid_token")

    expired = await auth_client.get(
        DRUG_TYPES_PATH, headers=auth_headers(token_for(admin_id, expires_minutes=-1))
    )
    assert_error_envelope(expired, 401, "invalid_token")

    for role, path in (("ADMIN", DRUG_TYPES_PATH), ("MEDICO", DRUG_TYPES_PATH)):
        inactive_id = await create_auth_user(
            db_conn,
            username=f"inactivo_{unique_suffix()}",
            role=role,
            is_active=False,
            password_hash=cached_password_hash(),
        )
        inactive_headers = auth_headers(token_for(inactive_id, role=role))
        list_response = await auth_client.get(path, headers=inactive_headers)
        assert_error_envelope(list_response, 403, "inactive_user")
        post_response = await auth_client.post(
            DRUG_TYPES_PATH, headers=inactive_headers, json={"description": "X"}
        )
        assert_error_envelope(post_response, 403, "inactive_user")


async def test_ca03_detail_of_missing_id_is_404_with_exact_envelope(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-3: `GET /{id}` inexistente ⇒ 404 `drug_type_not_found` en espanol."""
    _, headers = await admin_actor(db_conn)
    missing_id = await nonexistent_drug_type_id(db_conn)

    response = await auth_client.get(drug_type_path(missing_id), headers=headers)
    body = assert_error_envelope(response, 404, "drug_type_not_found")
    assert body["detail"] == "El tipo de fármaco no existe"
    assert body["errors"] == []
