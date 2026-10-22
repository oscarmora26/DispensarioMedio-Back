"""Integridad, envelope uniforme, logs y OpenAPI de FEAT-010 (CA-25 a CA-28)."""

from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.main import app
from tests.auth_support import auth_headers, cached_password_hash, create_auth_user, token_for
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
    nonexistent_catalog_id,
    nonexistent_medication_id,
)


class _ConstraintViolation(Exception):
    """Imita la excepción de `asyncpg` con el nombre de la constraint."""

    def __init__(self, constraint: str) -> None:
        super().__init__(f'duplicate key value violates unique constraint "{constraint}"')
        self.constraint_name = constraint


async def test_ca25_integrity_error_on_commit_becomes_409_and_session_survives(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CA-25: carrera en `uq_medications_identity` ⇒ 409 sin fila nueva ni sesión rota."""
    _, headers = await admin_actor(db_conn)
    drug_type_id, brand_id, location_id = await make_catalogs(db_conn)
    before = await count_medications(db_conn)

    async def failing_commit(session: AsyncSession) -> None:
        raise IntegrityError(
            "INSERT INTO medications ...", {}, _ConstraintViolation("uq_medications_identity")
        )

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)

    response = await auth_client.post(
        MEDICATIONS_PATH,
        headers=headers,
        json=medication_body(unique_suffix(), drug_type_id, brand_id, location_id),
    )
    body = assert_error_envelope(response, 409, "medication_exists")
    assert body["detail"] == "El medicamento ya existe"
    assert await count_medications(db_conn) == before

    usable = await auth_client.get(MEDICATIONS_PATH, headers=headers)
    assert usable.status_code == 200, usable.text
    assert "items" in usable.json()


async def test_ca26_every_error_uses_the_exact_envelope(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-26: cada código del catálogo de la regla 31 respeta `{detail, code, errors}`."""
    from sqlalchemy import text as _text

    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    drug_type_id, brand_id, location_id = await make_catalogs(db_conn)
    tag = unique_suffix()
    payload = medication_body(
        tag, drug_type_id, brand_id, location_id, description=f"Base {tag}", dose=f"500 mg {tag}"
    )
    created = await auth_client.post(MEDICATIONS_PATH, headers=admin_headers, json=payload)
    assert created.status_code == 201, created.text
    medication_id = created.json()["id"]
    missing_id = await nonexistent_medication_id(db_conn)
    missing_brand = await nonexistent_catalog_id(db_conn, "brands")
    inactive_id = await create_auth_user(
        db_conn,
        username=f"minactivo_{unique_suffix()}",
        role="ADMIN",
        is_active=False,
        password_hash=cached_password_hash(),
    )
    inactive_headers = auth_headers(token_for(inactive_id))

    other_drug, inactive_brand, other_loc = await make_catalogs(db_conn, suffix=unique_suffix())
    await db_conn.execute(
        _text("UPDATE brands SET is_active = false WHERE id = :id"), {"id": inactive_brand}
    )
    fk_inactive_payload = medication_body(unique_suffix(), other_drug, inactive_brand, other_loc)

    duplicate = dict(payload)
    duplicate["description"] = f"  {payload['description']}  "

    cases = [
        (await auth_client.get(MEDICATIONS_PATH), 401, "not_authenticated"),
        (
            await auth_client.get(MEDICATIONS_PATH, headers={"Authorization": "Bearer basura"}),
            401,
            "invalid_token",
        ),
        (await auth_client.get(MEDICATIONS_PATH, headers=inactive_headers), 403, "inactive_user"),
        (
            await auth_client.post(
                MEDICATIONS_PATH,
                headers=medico_headers,
                json=medication_body(unique_suffix(), drug_type_id, brand_id, location_id),
            ),
            403,
            "forbidden",
        ),
        (
            await auth_client.get(medication_path(missing_id), headers=admin_headers),
            404,
            "medication_not_found",
        ),
        (
            await auth_client.post(
                MEDICATIONS_PATH,
                headers=admin_headers,
                json={**payload, "description": f"  {payload['description']}  "},
            ),
            409,
            "medication_exists",
        ),
        (
            await auth_client.post(
                MEDICATIONS_PATH,
                headers=admin_headers,
                json={**payload, "brand_id": missing_brand},
            ),
            404,
            "brand_not_found",
        ),
        (
            await auth_client.post(
                MEDICATIONS_PATH,
                headers=admin_headers,
                json=fk_inactive_payload,
            ),
            422,
            "validation_error",
        ),
        (
            await auth_client.post(
                MEDICATIONS_PATH,
                headers=admin_headers,
                json={**payload, "description": ""},
            ),
            422,
            "validation_error",
        ),
        (
            await auth_client.get(f"{MEDICATIONS_PATH}/abc", headers=admin_headers),
            422,
            "validation_error",
        ),
        (
            await auth_client.put(
                medication_path(missing_id),
                headers=admin_headers,
                json=medication_body(unique_suffix(), drug_type_id, brand_id, location_id),
            ),
            404,
            "medication_not_found",
        ),
        (
            await auth_client.delete(medication_path(missing_id), headers=admin_headers),
            404,
            "medication_not_found",
        ),
        (
            await auth_client.delete(medication_path(medication_id), headers=medico_headers),
            403,
            "forbidden",
        ),
    ]
    for response, status, code in cases:
        body = assert_error_envelope(response, status, code)
        assert isinstance(body["errors"], list)
        assert "details" not in response.json()


async def test_ca27_all_events_carry_ids_and_never_leak_secrets(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-27: `created`/`updated`/`deactivated` con ids, sin tokens ni descripciones."""
    actor_id, headers = await admin_actor(db_conn)
    drug_type_id, brand_id, location_id = await make_catalogs(db_conn)
    tag = unique_suffix()
    caplog.set_level(logging.INFO)

    created = await auth_client.post(
        MEDICATIONS_PATH,
        headers=headers,
        json=medication_body(tag, drug_type_id, brand_id, location_id),
    )
    assert created.status_code == 201, created.text
    medication_id = created.json()["id"]

    updated = await auth_client.put(
        medication_path(medication_id),
        headers=headers,
        json=medication_body(tag, drug_type_id, brand_id, location_id, dose=f"750 mg {tag}"),
    )
    assert updated.status_code == 200, updated.text

    fetched = await fetch_medication_row(db_conn, medication_id)
    assert fetched is not None
    deactivated = await auth_client.put(
        medication_path(medication_id),
        headers=headers,
        json={
            "description": str(fetched["description"]),
            "drug_type_id": int(fetched["drug_type_id"]),
            "brand_id": int(fetched["brand_id"]),
            "location_id": int(fetched["location_id"]),
            "dose": str(fetched["dose"]),
            "is_active": False,
        },
    )
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["is_active"] is False

    events = {
        record.msg["event"]: record.msg
        for record in caplog.records
        if isinstance(record.msg, dict)
        and record.msg.get("event")
        in {"medication.created", "medication.updated", "medication.deactivated"}
    }
    assert set(events) == {"medication.created", "medication.updated", "medication.deactivated"}
    for event in events.values():
        assert event["medication_id"] == medication_id
        assert event["actor_id"] == actor_id

    logged = caplog.text
    assert f"Med {tag}" not in logged
    assert "request.completed" in logged


async def test_ca27_put_deactivation_emits_deactivated_instead_of_updated(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-27/regla 33: el paso a inactivo por PUT emite `medication.deactivated`."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    medication_id = await create_medication_row(db_conn, f"Vigente {tag}")
    before = await fetch_medication_row(db_conn, medication_id)
    assert before is not None
    caplog.set_level(logging.INFO)
    caplog.clear()

    response = await auth_client.put(
        medication_path(medication_id),
        headers=headers,
        json={
            "description": str(before["description"]),
            "drug_type_id": int(before["drug_type_id"]),
            "brand_id": int(before["brand_id"]),
            "location_id": int(before["location_id"]),
            "dose": str(before["dose"]),
            "is_active": False,
        },
    )
    assert response.status_code == 200, response.text

    names = [record.msg.get("event") for record in caplog.records if isinstance(record.msg, dict)]
    assert "medication.deactivated" in names
    assert "medication.updated" not in names


def test_ca28_openapi_declares_medications_contract() -> None:
    """CA-28: paths, tag, seguridad, documentación y códigos por endpoint."""
    spec = app.openapi()
    paths = spec["paths"]

    medication_paths = sorted(path for path in paths if path.startswith("/api/v1/medications"))
    assert medication_paths == ["/api/v1/medications", "/api/v1/medications/{medication_id}"]

    tags = sorted(
        {
            tag
            for path in medication_paths
            for method in paths[path].values()
            for tag in method.get("tags", [])
        }
    )
    assert tags == ["medications"]

    operations = [
        (path, verb, method) for path in medication_paths for verb, method in paths[path].items()
    ]
    assert len(operations) == 5
    for path, verb, method in operations:
        assert method.get("security"), (path, verb)
        assert method.get("summary"), (path, verb)
        assert method.get("description"), (path, verb)

    assert "content" in paths["/api/v1/medications"]["get"]["responses"]["200"]
    assert "content" in paths["/api/v1/medications"]["post"]["responses"]["201"]

    expected = {
        ("/api/v1/medications", "get"): ["200", "401", "403", "422"],
        ("/api/v1/medications", "post"): ["201", "401", "403", "404", "409", "422"],
        ("/api/v1/medications/{medication_id}", "get"): ["200", "401", "403", "404", "422"],
        ("/api/v1/medications/{medication_id}", "put"): [
            "200",
            "401",
            "403",
            "404",
            "409",
            "422",
        ],
        ("/api/v1/medications/{medication_id}", "delete"): ["204", "401", "403", "404", "422"],
    }
    for (path, verb), codes in expected.items():
        assert sorted(paths[path][verb]["responses"]) == codes, (path, verb)
