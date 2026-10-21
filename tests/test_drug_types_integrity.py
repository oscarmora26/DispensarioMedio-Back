"""Integridad, envelope uniforme, logs y OpenAPI de FEAT-007 (CA-23 a CA-26)."""

from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.main import app
from tests.auth_support import auth_headers, create_auth_user, token_for
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


class _ConstraintViolation(Exception):
    """Imita la excepcion de `asyncpg` con el nombre de la constraint."""

    def __init__(self, constraint: str) -> None:
        super().__init__(f'duplicate key value violates unique constraint "{constraint}"')
        self.constraint_name = constraint


async def test_ca23_integrity_error_on_commit_becomes_409_and_session_survives(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CA-23: carrera en `uq_drug_types_description` ⇒ 409 sin fila nueva ni sesion rota."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    description = f"Carrera {tag}"
    before = await count_drug_types(db_conn)

    async def failing_commit(session: AsyncSession) -> None:
        raise IntegrityError(
            "INSERT INTO drug_types ...", {}, _ConstraintViolation("uq_drug_types_description")
        )

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)

    response = await auth_client.post(
        DRUG_TYPES_PATH, headers=headers, json={"description": description}
    )
    body = assert_error_envelope(response, 409, "drug_type_exists")
    assert body["detail"] == "El tipo de fármaco ya existe"
    assert await count_drug_types(db_conn) == before

    usable = await auth_client.get(DRUG_TYPES_PATH, headers=headers)
    assert usable.status_code == 200, usable.text
    assert "items" in usable.json()


async def test_ca24_every_error_uses_the_exact_envelope(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-24: cada codigo del catalogo de la regla 30 respeta `{detail, code, errors}`."""
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    tag = unique_suffix()
    description = f"Unico {tag}"
    created = await auth_client.post(
        DRUG_TYPES_PATH, headers=admin_headers, json={"description": description}
    )
    assert created.status_code == 201, created.text
    drug_type_id = created.json()["id"]
    missing_id = await nonexistent_drug_type_id(db_conn)
    inactive_id = await create_auth_user(
        db_conn, username=f"inactivo_{unique_suffix()}", role="ADMIN", is_active=False
    )
    inactive_headers = auth_headers(token_for(inactive_id))

    cases = [
        (await auth_client.get(DRUG_TYPES_PATH), 401, "not_authenticated"),
        (
            await auth_client.get(DRUG_TYPES_PATH, headers={"Authorization": "Bearer basura"}),
            401,
            "invalid_token",
        ),
        (await auth_client.get(DRUG_TYPES_PATH, headers=inactive_headers), 403, "inactive_user"),
        (
            await auth_client.post(
                DRUG_TYPES_PATH, headers=medico_headers, json={"description": f"Otro {tag}"}
            ),
            403,
            "forbidden",
        ),
        (
            await auth_client.get(drug_type_path(missing_id), headers=admin_headers),
            404,
            "drug_type_not_found",
        ),
        (
            await auth_client.post(
                DRUG_TYPES_PATH, headers=admin_headers, json={"description": description.upper()}
            ),
            409,
            "drug_type_exists",
        ),
        (
            await auth_client.post(
                DRUG_TYPES_PATH, headers=admin_headers, json={"description": ""}
            ),
            422,
            "validation_error",
        ),
        (
            await auth_client.get(f"{DRUG_TYPES_PATH}/abc", headers=admin_headers),
            422,
            "validation_error",
        ),
        (
            await auth_client.put(
                drug_type_path(missing_id),
                headers=admin_headers,
                json={"description": f"Otro {tag}", "is_active": True},
            ),
            404,
            "drug_type_not_found",
        ),
        (
            await auth_client.delete(drug_type_path(missing_id), headers=admin_headers),
            404,
            "drug_type_not_found",
        ),
        (
            await auth_client.delete(drug_type_path(drug_type_id), headers=medico_headers),
            403,
            "forbidden",
        ),
    ]
    for response, status, code in cases:
        body = assert_error_envelope(response, status, code)
        assert isinstance(body["errors"], list)


async def test_ca25_all_events_carry_ids_and_never_leak_secrets(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-25: `created`/`updated`/`deactivated` con ids, sin tokens ni descripciones."""
    actor_id, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    caplog.set_level(logging.INFO)

    created = await auth_client.post(
        DRUG_TYPES_PATH, headers=headers, json={"description": f"Eventos {tag}"}
    )
    assert created.status_code == 201, created.text
    drug_type_id = created.json()["id"]

    updated = await auth_client.put(
        drug_type_path(drug_type_id),
        headers=headers,
        json={"description": f"Eventos {tag}", "is_active": True},
    )
    assert updated.status_code == 200, updated.text

    deactivated = await auth_client.put(
        drug_type_path(drug_type_id),
        headers=headers,
        json={"description": f"Eventos {tag}", "is_active": False},
    )
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["is_active"] is False

    events = {
        record.msg["event"]: record.msg
        for record in caplog.records
        if isinstance(record.msg, dict)
        and record.msg.get("event")
        in {"drug_type.created", "drug_type.updated", "drug_type.deactivated"}
    }
    assert set(events) == {"drug_type.created", "drug_type.updated", "drug_type.deactivated"}
    for event in events.values():
        assert event["drug_type_id"] == drug_type_id
        assert event["actor_id"] == actor_id

    logged = caplog.text
    assert f"Eventos {tag}" not in logged
    assert "request.completed" in logged


async def test_ca25_put_deactivation_emits_deactivated_instead_of_updated(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-25/regla 33: el paso a inactivo por PUT emite `drug_type.deactivated`."""
    _, headers = await admin_actor(db_conn)
    drug_type_id = await create_drug_type_row(db_conn, f"Vigente {unique_suffix()}")
    before = await fetch_drug_type_row(db_conn, drug_type_id)
    assert before is not None
    caplog.set_level(logging.INFO)
    caplog.clear()

    response = await auth_client.put(
        drug_type_path(drug_type_id),
        headers=headers,
        json={"description": before["description"], "is_active": False},
    )
    assert response.status_code == 200, response.text

    names = [record.msg.get("event") for record in caplog.records if isinstance(record.msg, dict)]
    assert "drug_type.deactivated" in names
    assert "drug_type.updated" not in names


def test_ca26_openapi_declares_drug_types_contract() -> None:
    """CA-26: paths, tag, seguridad, documentacion y codigos por endpoint."""
    spec = app.openapi()
    paths = spec["paths"]

    drug_paths = sorted(path for path in paths if path.startswith("/api/v1/drug-types"))
    assert drug_paths == ["/api/v1/drug-types", "/api/v1/drug-types/{drug_type_id}"]

    tags = sorted(
        {
            tag
            for path in drug_paths
            for method in paths[path].values()
            for tag in method.get("tags", [])
        }
    )
    assert tags == ["drug-types"]

    operations = [
        (path, verb, method) for path in drug_paths for verb, method in paths[path].items()
    ]
    assert len(operations) == 5
    for path, verb, method in operations:
        assert method.get("security"), (path, verb)
        assert method.get("summary"), (path, verb)
        assert method.get("description"), (path, verb)

    assert "content" in paths["/api/v1/drug-types"]["get"]["responses"]["200"]
    assert "content" in paths["/api/v1/drug-types"]["post"]["responses"]["201"]

    expected = {
        ("/api/v1/drug-types", "get"): ["200", "401", "403", "422"],
        ("/api/v1/drug-types", "post"): ["201", "401", "403", "409", "422"],
        ("/api/v1/drug-types/{drug_type_id}", "get"): ["200", "401", "403", "404", "422"],
        ("/api/v1/drug-types/{drug_type_id}", "put"): [
            "200",
            "401",
            "403",
            "404",
            "409",
            "422",
        ],
        ("/api/v1/drug-types/{drug_type_id}", "delete"): ["204", "401", "403", "404", "422"],
    }
    for (path, verb), codes in expected.items():
        assert sorted(paths[path][verb]["responses"]) == codes, (path, verb)
