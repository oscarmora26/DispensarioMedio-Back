"""Unitarias sin base de datos de FEAT-013 (validadores, helpers y OpenAPI)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import AppError
from app.main import app
from app.schemas.visit import (
    DUPLICATE_MEDICATION_MESSAGE,
    INSTRUCTIONS_MAX_MESSAGE,
    QUANTITY_POSITIVE_MESSAGE,
    REASON_MAX_MESSAGE,
    REASON_REQUIRED_MESSAGE,
    RECOMMENDATIONS_MAX_MESSAGE,
    SORT_FIELDS,
    SORT_PATTERN,
    SYMPTOMS_MAX_MESSAGE,
    SYMPTOMS_RANGE_MESSAGE,
    VISIT_STATUS_PATTERN,
    VISIT_STATUS_VALUES,
    VisitAnnulRequest,
    VisitCreate,
    VisitMedicationInput,
)
from app.services.visit_service import (
    ANNULMENT_CHECK,
    QUANTITY_CHECK,
    STATUS_CHECK,
    UNIQUE_VISIT_MEDICATION,
    calculate_pages,
    normalize_medications,
    parse_sort,
    to_utc,
    translate_integrity_error,
)

VISITS_OPENAPI_PATH = "/api/v1/visits"
VISIT_OPENAPI_PATH = "/api/v1/visits/{visit_id}"
ANNUL_OPENAPI_PATH = "/api/v1/visits/{visit_id}/annul"


def _assert_invalid(error: AppError, field: str, message: str) -> None:
    """Comprueba el 422 de campo unico que devuelve el servicio."""
    assert error.status_code == 422
    assert error.code == "validation_error"
    assert error.errors == [{"field": field, "message": message}]


def _field_of(exc: ValidationError) -> tuple[str, ...]:
    """Devuelve el `loc` del unico error de validacion."""
    assert len(exc.errors()) == 1, exc.errors()
    return tuple(exc.errors()[0]["loc"])


def _message_of(exc: ValidationError) -> str:
    """Devuelve el mensaje del unico error de validacion."""
    assert len(exc.errors()) == 1, exc.errors()
    return str(exc.errors()[0]["msg"])


def test_ca20_parse_sort_catalog_and_direction() -> None:
    """CA-20: catalogo cerrado de `sort` con prefijo `-` para descendente."""
    assert parse_sort("visited_at") == ("visited_at", False)
    assert parse_sort("-visited_at") == ("visited_at", True)
    assert parse_sort("-id") == ("id", True)
    for field in SORT_FIELDS:
        assert parse_sort(field)[0] == field

    with pytest.raises(AppError) as invalid:
        parse_sort("doctor_name")
    _assert_invalid(invalid.value, "sort", "Valor de orden no permitido")


def test_ca20_sort_pattern_and_status_catalog_match_the_documented_closed_sets() -> None:
    """CA-20/CA-23: `SORT_PATTERN` y `VISIT_STATUS_PATTERN` son catalogos cerrados."""
    import re

    pattern = re.compile(SORT_PATTERN)
    for field in SORT_FIELDS:
        assert pattern.fullmatch(field)
        assert pattern.fullmatch(f"-{field}")
    for rejected in ("doctor", "visited_at asc", "", "-", "status-"):
        assert pattern.fullmatch(rejected) is None

    status_pattern = re.compile(VISIT_STATUS_PATTERN)
    assert VISIT_STATUS_VALUES == ("REGISTRADA", "ANULADA")
    for value in VISIT_STATUS_VALUES:
        assert status_pattern.fullmatch(value)
    assert status_pattern.fullmatch("registrada") is None
    assert status_pattern.fullmatch("ANULANDO") is None


def test_ca16_calculate_pages_formula() -> None:
    """CA-16: `pages` es 0 sin filas y redondea hacia arriba con filas."""
    assert calculate_pages(0, 20) == 0
    assert calculate_pages(1, 20) == 1
    assert calculate_pages(20, 20) == 1
    assert calculate_pages(21, 20) == 2
    assert calculate_pages(100, 10) == 10


def test_d3_naive_dates_are_assumed_in_santo_domingo() -> None:
    """D-3: una fecha sin zona se interpreta en `America/Santo_Domingo` (UTC-4)."""
    naive = datetime(2026, 10, 21, 10, 30)
    assert to_utc(naive) == datetime(2026, 10, 21, 14, 30, tzinfo=UTC)

    santo_domingo = timezone(timedelta(hours=-4))
    assert to_utc(datetime(2026, 10, 21, 10, 30, tzinfo=santo_domingo)) == datetime(
        2026, 10, 21, 14, 30, tzinfo=UTC
    )
    assert to_utc(datetime(2026, 10, 21, 14, 30, tzinfo=UTC)) == datetime(
        2026, 10, 21, 14, 30, tzinfo=UTC
    )


def test_ca8_symptoms_are_trimmed_and_bounded() -> None:
    """CA-8: `symptoms` se recorta y exige entre 3 y 2000 caracteres."""
    created = VisitCreate(symptoms="  Dolor de cabeza  ")
    assert created.symptoms == "Dolor de cabeza"

    for invalid in ("", "  ", "ab", " a "):
        with pytest.raises(ValidationError) as excinfo:
            VisitCreate(symptoms=invalid)
        assert _field_of(excinfo.value) == ("symptoms",)
        assert _message_of(excinfo.value) == SYMPTOMS_RANGE_MESSAGE

    with pytest.raises(ValidationError) as too_long:
        VisitCreate(symptoms="a" * 2001)
    assert _field_of(too_long.value) == ("symptoms",)
    assert _message_of(too_long.value) == SYMPTOMS_MAX_MESSAGE

    assert VisitCreate(symptoms="abc").symptoms == "abc"
    assert VisitCreate(symptoms="a" * 2000).symptoms == "a" * 2000


def test_ca9_ca14_recommendations_are_trimmed_and_blank_becomes_null() -> None:
    """CA-9/CA-14: `recommendations` vacio o de solo espacios es `null`."""
    assert VisitCreate(symptoms="abc").recommendations is None
    assert VisitCreate(symptoms="abc", recommendations=None).recommendations is None
    assert VisitCreate(symptoms="abc", recommendations="   ").recommendations is None
    assert VisitCreate(symptoms="abc", recommendations=" Reposo ").recommendations == "Reposo"
    assert VisitCreate(symptoms="abc", recommendations="a" * 2000).recommendations == "a" * 2000

    with pytest.raises(ValidationError) as too_long:
        VisitCreate(symptoms="abc", recommendations="a" * 2001)
    assert _field_of(too_long.value) == ("recommendations",)
    assert _message_of(too_long.value) == RECOMMENDATIONS_MAX_MESSAGE


def test_ca11_medications_are_optional_and_instructions_are_trimmed() -> None:
    """CA-11: la lista es opcional; `instructions` vacio se guarda como `null`."""
    assert VisitCreate(symptoms="abc").medications is None
    assert normalize_medications(None) == []
    assert normalize_medications([]) == []

    item = VisitMedicationInput(medication_id=8, quantity=Decimal("2"), instructions="  ")
    assert item.instructions is None
    normalized = normalize_medications([item])
    assert normalized[0].instructions is None
    assert VisitMedicationInput(medication_id=8, instructions=" Cada 8 horas ").instructions == (
        "Cada 8 horas"
    )


def test_ca11_duplicate_medication_is_rejected() -> None:
    """CA-11: un `medication_id` repetido en la lista ⇒ 422 en `medications`."""
    duplicate = [
        VisitMedicationInput(medication_id=8),
        VisitMedicationInput(medication_id=8, quantity=Decimal("1")),
    ]
    with pytest.raises(AppError) as excinfo:
        normalize_medications(duplicate)
    _assert_invalid(excinfo.value, "medications", DUPLICATE_MEDICATION_MESSAGE)


def test_ca11_quantity_must_be_positive() -> None:
    """CA-11: `quantity` presente y ≤ 0 ⇒ 422 en `quantity`."""
    for invalid in (Decimal("0"), Decimal("-1"), Decimal("-0.01")):
        with pytest.raises(AppError) as excinfo:
            normalize_medications([VisitMedicationInput(medication_id=8, quantity=invalid)])
        _assert_invalid(excinfo.value, "quantity", QUANTITY_POSITIVE_MESSAGE)

    assert normalize_medications([VisitMedicationInput(medication_id=8, quantity=Decimal("0.01"))])


def test_ca11_instructions_are_bounded() -> None:
    """CA-11: `instructions` mayor a 255 caracteres ⇒ 422 en `instructions`."""
    with pytest.raises(AppError) as excinfo:
        normalize_medications([VisitMedicationInput(medication_id=8, instructions="a" * 256)])
    _assert_invalid(excinfo.value, "instructions", INSTRUCTIONS_MAX_MESSAGE)

    assert normalize_medications([VisitMedicationInput(medication_id=8, instructions="a" * 255)])


def test_ca30_annul_reason_is_required_trimmed_and_bounded() -> None:
    """CA-30: `reason` obligatorio, recortado y de 500 caracteres como maximo."""
    assert VisitAnnulRequest(reason="  Visita registrada por error  ").reason == (
        "Visita registrada por error"
    )

    for invalid in ("", "   "):
        with pytest.raises(ValidationError) as excinfo:
            VisitAnnulRequest(reason=invalid)
        assert _field_of(excinfo.value) == ("reason",)
        assert _message_of(excinfo.value) == REASON_REQUIRED_MESSAGE

    with pytest.raises(ValidationError) as too_long:
        VisitAnnulRequest(reason="a" * 501)
    assert _field_of(too_long.value) == ("reason",)
    assert _message_of(too_long.value) == REASON_MAX_MESSAGE

    assert VisitAnnulRequest(reason="a" * 500).reason == "a" * 500


def _integrity_error(constraint: str) -> IntegrityError:
    """Construye un `IntegrityError` como el que produce el driver."""
    return IntegrityError(
        "INSERT INTO visits ...",
        {},
        Exception(f'duplicate key value violates unique constraint "{constraint}"'),
    )


def test_ca33_known_constraints_are_translated_to_field_errors() -> None:
    """CA-33: D-9 traduce las cuatro constraints a su 422 de campo."""
    expected = {
        UNIQUE_VISIT_MEDICATION: ("medications", DUPLICATE_MEDICATION_MESSAGE),
        QUANTITY_CHECK: ("quantity", QUANTITY_POSITIVE_MESSAGE),
        STATUS_CHECK: ("status", "El estado de la visita no es válido"),
        ANNULMENT_CHECK: ("status", "Los datos de anulación no son válidos"),
    }
    for constraint, (field, message) in expected.items():
        error = translate_integrity_error(_integrity_error(constraint))
        assert isinstance(error, AppError)
        _assert_invalid(error, field, message)


def test_ca33_unknown_constraints_are_not_translated() -> None:
    """CA-33: una constraint desconocida no se traduce y sube tal cual."""
    assert translate_integrity_error(_integrity_error("uq_doctors_cedula")) is None


def test_ca35_openapi_documents_the_five_operations() -> None:
    """CA-35: las cinco rutas declaran tag `visits`, `summary`, `description` y codigos."""
    paths = app.openapi()["paths"]
    operations = {
        (VISITS_OPENAPI_PATH, "get"): {200, 401, 403, 422},
        (VISITS_OPENAPI_PATH, "post"): {201, 401, 403, 404, 422},
        (VISIT_OPENAPI_PATH, "get"): {200, 401, 403, 404, 422},
        (VISIT_OPENAPI_PATH, "put"): {200, 401, 403, 404, 409, 422},
        (ANNUL_OPENAPI_PATH, "post"): {200, 401, 403, 404, 409, 422},
    }
    for (path, method), codes in operations.items():
        operation = paths[path][method]
        assert operation["tags"] == ["visits"], (path, method)
        assert operation.get("summary"), (path, method)
        assert operation.get("description"), (path, method)
        document = {int(code) for code in operation["responses"]}
        assert codes <= document, (path, method, document)

    sort_parameter = next(
        parameter
        for parameter in paths[VISITS_OPENAPI_PATH]["get"]["parameters"]
        if parameter["name"] == "sort"
    )
    assert sort_parameter["schema"]["pattern"] == SORT_PATTERN

    status_parameter = next(
        parameter
        for parameter in paths[VISITS_OPENAPI_PATH]["get"]["parameters"]
        if parameter["name"] == "status"
    )
    assert status_parameter["schema"]["anyOf"][0]["pattern"] == VISIT_STATUS_PATTERN


def test_ca35_write_descriptions_do_not_advertise_admin_writes() -> None:
    """CA-35/FEAT-019: las tres escrituras se documentan como exclusivas de MEDICO."""
    paths = app.openapi()["paths"]
    write_operations = [
        (VISITS_OPENAPI_PATH, "post"),
        (VISIT_OPENAPI_PATH, "put"),
        (ANNUL_OPENAPI_PATH, "post"),
    ]
    for path, method in write_operations:
        description = paths[path][method]["description"]
        assert "MEDICO" in description, (path, method)
        for stale in ("Accesible para ADMIN", "con token ADMIN"):
            assert stale not in description, (path, method, stale)
