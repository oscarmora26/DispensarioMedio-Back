"""Integridad, envelope uniforme, logs y OpenAPI de FEAT-009 (CA-24 a CA-27)."""

from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.main import app
from tests.auth_support import auth_headers, cached_password_hash, create_auth_user, token_for
from tests.db_support import unique_suffix
from tests.locations_support import (
    LOCATIONS_PATH,
    admin_actor,
    assert_error_envelope,
    count_locations,
    create_location_row,
    fetch_location_row,
    location_body,
    location_path,
    medico_actor,
    nonexistent_location_id,
)


class _ConstraintViolation(Exception):
    """Imita la excepción de `asyncpg` con el nombre de la constraint."""

    def __init__(self, constraint: str) -> None:
        super().__init__(f'duplicate key value violates unique constraint "{constraint}"')
        self.constraint_name = constraint


async def test_ca24_integrity_error_on_commit_becomes_409_and_session_survives(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CA-24: carrera en `uq_locations_position` ⇒ 409 sin fila nueva ni sesión rota."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    before = await count_locations(db_conn)

    async def failing_commit(session: AsyncSession) -> None:
        raise IntegrityError(
            "INSERT INTO locations ...", {}, _ConstraintViolation("uq_locations_position")
        )

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)

    response = await auth_client.post(LOCATIONS_PATH, headers=headers, json=location_body(tag))
    body = assert_error_envelope(response, 409, "location_exists")
    assert body["detail"] == "La ubicación ya existe"
    assert await count_locations(db_conn) == before

    usable = await auth_client.get(LOCATIONS_PATH, headers=headers)
    assert usable.status_code == 200, usable.text
    assert "items" in usable.json()


async def test_ca25_every_error_uses_the_exact_envelope(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-25: cada código del catálogo de la regla 30 respeta `{detail, code, errors}`."""
    _, admin_headers = await admin_actor(db_conn)
    _, medico_headers = await medico_actor(db_conn)
    tag = unique_suffix()
    payload = location_body(tag)
    created = await auth_client.post(LOCATIONS_PATH, headers=admin_headers, json=payload)
    assert created.status_code == 201, created.text
    location_id = created.json()["id"]
    missing_id = await nonexistent_location_id(db_conn)
    inactive_id = await create_auth_user(
        db_conn,
        username=f"linactivo_{unique_suffix()}",
        role="ADMIN",
        is_active=False,
        password_hash=cached_password_hash(),
    )
    inactive_headers = auth_headers(token_for(inactive_id))

    duplicate = dict(payload)
    duplicate["shelf"] = str(payload["shelf"]).upper()
    duplicate["description"] = f"Gemela {tag}"

    cases = [
        (await auth_client.get(LOCATIONS_PATH), 401, "not_authenticated"),
        (
            await auth_client.get(LOCATIONS_PATH, headers={"Authorization": "Bearer basura"}),
            401,
            "invalid_token",
        ),
        (await auth_client.get(LOCATIONS_PATH, headers=inactive_headers), 403, "inactive_user"),
        (
            await auth_client.post(
                LOCATIONS_PATH, headers=medico_headers, json=location_body(unique_suffix())
            ),
            403,
            "forbidden",
        ),
        (
            await auth_client.get(location_path(missing_id), headers=admin_headers),
            404,
            "location_not_found",
        ),
        (
            await auth_client.post(LOCATIONS_PATH, headers=admin_headers, json=duplicate),
            409,
            "location_exists",
        ),
        (
            await auth_client.post(
                LOCATIONS_PATH, headers=admin_headers, json={**payload, "shelf": ""}
            ),
            422,
            "validation_error",
        ),
        (
            await auth_client.get(f"{LOCATIONS_PATH}/abc", headers=admin_headers),
            422,
            "validation_error",
        ),
        (
            await auth_client.put(
                location_path(missing_id),
                headers=admin_headers,
                json=location_body(unique_suffix()),
            ),
            404,
            "location_not_found",
        ),
        (
            await auth_client.delete(location_path(missing_id), headers=admin_headers),
            404,
            "location_not_found",
        ),
        (
            await auth_client.delete(location_path(location_id), headers=medico_headers),
            403,
            "forbidden",
        ),
    ]
    for response, status, code in cases:
        body = assert_error_envelope(response, status, code)
        assert isinstance(body["errors"], list)


async def test_ca26_all_events_carry_ids_and_never_leak_secrets(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-26: `created`/`updated`/`deactivated` con ids, sin tokens ni posiciones."""
    actor_id, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    caplog.set_level(logging.INFO)

    created = await auth_client.post(LOCATIONS_PATH, headers=headers, json=location_body(tag))
    assert created.status_code == 201, created.text
    location_id = created.json()["id"]

    updated = await auth_client.put(
        location_path(location_id), headers=headers, json=location_body(tag)
    )
    assert updated.status_code == 200, updated.text

    fetched = await fetch_location_row(db_conn, location_id)
    assert fetched is not None
    deactivated = await auth_client.put(
        location_path(location_id),
        headers=headers,
        json={
            "description": str(fetched["description"]),
            "shelf": str(fetched["shelf"]),
            "section": str(fetched["section"]),
            "cell": str(fetched["cell"]),
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
        in {"location.created", "location.updated", "location.deactivated"}
    }
    assert set(events) == {"location.created", "location.updated", "location.deactivated"}
    for event in events.values():
        assert event["location_id"] == location_id
        assert event["actor_id"] == actor_id

    logged = caplog.text
    assert f"Estante {tag}" not in logged
    assert f"A-{tag}" not in logged
    assert "request.completed" in logged


async def test_ca26_put_deactivation_emits_deactivated_instead_of_updated(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CA-26/regla 33: el paso a inactivo por PUT emite `location.deactivated`."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    location_id = await create_location_row(db_conn, f"Vigente {tag}")
    before = await fetch_location_row(db_conn, location_id)
    assert before is not None
    caplog.set_level(logging.INFO)
    caplog.clear()

    response = await auth_client.put(
        location_path(location_id),
        headers=headers,
        json={
            "description": str(before["description"]),
            "shelf": str(before["shelf"]),
            "section": str(before["section"]),
            "cell": str(before["cell"]),
            "is_active": False,
        },
    )
    assert response.status_code == 200, response.text

    names = [record.msg.get("event") for record in caplog.records if isinstance(record.msg, dict)]
    assert "location.deactivated" in names
    assert "location.updated" not in names


def test_ca27_openapi_declares_locations_contract() -> None:
    """CA-27: paths, tag, seguridad, documentación y códigos por endpoint."""
    spec = app.openapi()
    paths = spec["paths"]

    location_paths = sorted(path for path in paths if path.startswith("/api/v1/locations"))
    assert location_paths == ["/api/v1/locations", "/api/v1/locations/{location_id}"]

    tags = sorted(
        {
            tag
            for path in location_paths
            for method in paths[path].values()
            for tag in method.get("tags", [])
        }
    )
    assert tags == ["locations"]

    operations = [
        (path, verb, method) for path in location_paths for verb, method in paths[path].items()
    ]
    assert len(operations) == 5
    for path, verb, method in operations:
        assert method.get("security"), (path, verb)
        assert method.get("summary"), (path, verb)
        assert method.get("description"), (path, verb)

    assert "content" in paths["/api/v1/locations"]["get"]["responses"]["200"]
    assert "content" in paths["/api/v1/locations"]["post"]["responses"]["201"]

    expected = {
        ("/api/v1/locations", "get"): ["200", "401", "403", "422"],
        ("/api/v1/locations", "post"): ["201", "401", "403", "409", "422"],
        ("/api/v1/locations/{location_id}", "get"): ["200", "401", "403", "404", "422"],
        ("/api/v1/locations/{location_id}", "put"): [
            "200",
            "401",
            "403",
            "404",
            "409",
            "422",
        ],
        ("/api/v1/locations/{location_id}", "delete"): ["204", "401", "403", "404", "422"],
    }
    for (path, verb), codes in expected.items():
        assert sorted(paths[path][verb]["responses"]) == codes, (path, verb)
