"""Integridad, envelope uniforme y OpenAPI de FEAT-011 (CA-32 a CA-34)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from tests.db_support import unique_cedula, unique_suffix
from tests.patients_support import (
    FORBIDDEN_KEYS,
    ITEM_KEYS,
    PATIENTS_PATH,
    admin_actor,
    assert_error_envelope,
    count_patients,
    create_patient_row,
    patient_body,
    patient_path,
)


class _ConstraintViolation(Exception):
    """Imita la excepcion de `asyncpg` con el nombre de la constraint."""

    def __init__(self, constraint: str) -> None:
        super().__init__(f'duplicate key value violates unique constraint "{constraint}"')
        self.constraint_name = constraint


@pytest.mark.parametrize(
    ("constraint", "code", "detail"),
    [
        ("uq_patients_cedula", "patient_exists", "El paciente ya existe"),
        ("uq_patients_carnet", "patient_carnet_exists", "El carnet ya está registrado"),
    ],
)
async def test_ca33_integrity_error_on_commit_becomes_409_and_session_survives(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    monkeypatch: pytest.MonkeyPatch,
    constraint: str,
    code: str,
    detail: str,
) -> None:
    """CA-33: carrera en una constraint unica ⇒ 409 sin fila nueva ni sesion rota."""
    _, headers = await admin_actor(db_conn)
    tag = unique_suffix()
    before = await count_patients(db_conn)

    async def failing_commit(session: AsyncSession) -> None:
        raise IntegrityError("INSERT INTO patients ...", {}, _ConstraintViolation(constraint))

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)

    response = await auth_client.post(
        PATIENTS_PATH, headers=headers, json=patient_body(tag, carnet=f"Z{tag}")
    )
    body = assert_error_envelope(response, 409, code)
    assert body["detail"] == detail
    assert await count_patients(db_conn) == before


async def test_ca33_check_violation_on_commit_becomes_422(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-11: una violacion de `CHECK` llegada por el `commit` se traduce a 422."""
    _, headers = await admin_actor(db_conn)

    async def failing_commit(session: AsyncSession) -> None:
        raise IntegrityError(
            "INSERT INTO patients ...",
            {},
            _ConstraintViolation("ck_patients_carnet_required_for_student"),
        )

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)

    response = await auth_client.post(
        PATIENTS_PATH, headers=headers, json=patient_body(unique_suffix(), carnet="K1")
    )
    body = assert_error_envelope(response, 422, "validation_error")
    assert body["errors"] == [
        {"field": "carnet", "message": "El carnet es obligatorio para estudiantes"}
    ]


async def test_ca32_error_envelope_is_uniform(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-32: todos los errores traen `detail`, `code` y `errors`, nunca `details`."""
    _, headers = await admin_actor(db_conn)
    patient_id = await create_patient_row(db_conn, name=f"Envelope {unique_suffix()}")

    responses = [
        await auth_client.get(patient_path(patient_id + 999_999), headers=headers),
        await auth_client.post(PATIENTS_PATH, headers=headers, json={"name": "X"}),
        await auth_client.get(PATIENTS_PATH, headers=headers, params={"size": 999}),
    ]
    assert {response.status_code for response in responses} == {404, 422}

    for response in responses:
        body = response.json()
        assert set(body) == {"detail", "code", "errors"}, body
        assert isinstance(body["errors"], list)
        for error in body["errors"]:
            assert set(error) == {"field", "message"}


async def test_ca34_openapi_documents_patient_endpoints(
    auth_client: AsyncClient,
) -> None:
    """CA-34: `/openapi.json` documenta las cinco rutas con su tag y respuestas."""
    schema = (await auth_client.get("/openapi.json")).json()
    paths = schema["paths"]

    operations = {
        ("get", PATIENTS_PATH): "200",
        ("post", PATIENTS_PATH): "201",
        ("get", patient_path(1).replace("/1", "/{patient_id}")): "200",
        ("put", "/api/v1/patients/{patient_id}"): "200",
        ("delete", "/api/v1/patients/{patient_id}"): "204",
    }
    for (method, path), success in operations.items():
        assert path in paths, path
        operation = paths[path][method]
        assert operation["tags"] == ["patients"]
        assert operation["summary"]
        assert operation["description"]
        assert success in operation["responses"]
        assert "422" in operation["responses"]

    assert "409" in paths[PATIENTS_PATH]["post"]["responses"]
    assert "404" in paths["/api/v1/patients/{patient_id}"]["get"]["responses"]

    schemas = schema["components"]["schemas"]
    assert set(schemas["PatientRead"]["properties"]) == ITEM_KEYS
    assert FORBIDDEN_KEYS.isdisjoint(schemas["PatientRead"]["properties"])

    list_responses = paths[PATIENTS_PATH]["get"]["responses"]["200"]["content"]["application/json"]
    assert list_responses["schema"]["$ref"].endswith("/PatientListResponse")

    sort_param = next(
        parameter
        for parameter in paths[PATIENTS_PATH]["get"]["parameters"]
        if parameter["name"] == "sort"
    )
    assert sort_param["schema"]["pattern"] == "^-?(?:name|cedula|created_at|is_active|id)$"


async def test_rf18_cedula_check_is_enforced_by_the_database(
    db_conn: AsyncConnection,
) -> None:
    """RF-18: la base reimpone el formato de cedula (defensa en profundidad)."""
    from sqlalchemy.exc import IntegrityError as SAIntegrityError

    from tests.db_support import create_patient

    with pytest.raises(SAIntegrityError):
        async with db_conn.begin_nested():
            await create_patient(db_conn, cedula="123")

    with pytest.raises(SAIntegrityError):
        async with db_conn.begin_nested():
            await create_patient(db_conn, patient_type="ESTUDIANTE", carnet=None)

    assert unique_cedula().isdigit()
