"""Listado de visitas de FEAT-013 (CA-16 a CA-22)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from app.schemas.visit import DOCTOR_FILTER_FORBIDDEN_MESSAGE
from tests.visits_support import (
    DOCTOR_REF_KEYS,
    LIST_ITEM_KEYS,
    PATIENT_REF_KEYS,
    VISITS_PATH,
    admin_actor,
    annul_visit_row,
    assert_error_envelope,
    assert_field_error,
    create_medication_row,
    doctor_row,
    medico_actor,
    patient_row,
    register_visit,
)

FIRST_VISIT_AT = datetime(2026, 1, 15, 14, 0, tzinfo=UTC)
SECOND_VISIT_AT = datetime(2026, 1, 16, 14, 0, tzinfo=UTC)
THIRD_VISIT_AT = datetime(2026, 1, 17, 14, 0, tzinfo=UTC)


async def test_ca16_pagination_envelope_and_empty_page(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-16: envelope `items/total/page/size/pages`; `pages == 0` sin filas."""
    admin_id, headers = await admin_actor(db_conn)
    doctor_id = await doctor_row(db_conn)
    patient_id = await patient_row(db_conn)
    empty_patient_id = await patient_row(db_conn)
    await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
    )
    await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
    )

    listing = await auth_client.get(VISITS_PATH, headers=headers, params={"size": 1})
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert set(body) == {"items", "total", "page", "size", "pages"}
    assert body["total"] == 2
    assert body["page"] == 1
    assert body["size"] == 1
    assert body["pages"] == 2
    assert len(body["items"]) == 1

    empty = await auth_client.get(
        VISITS_PATH, headers=headers, params={"patient_id": empty_patient_id}
    )
    assert empty.status_code == 200, empty.text
    assert empty.json() == {"items": [], "total": 0, "page": 1, "size": 20, "pages": 0}

    far = await auth_client.get(VISITS_PATH, headers=headers, params={"page": 99, "size": 20})
    assert far.status_code == 200, far.text
    assert far.json()["items"] == []
    assert far.json()["total"] == 2
    assert far.json()["page"] == 99

    for params in ({"page": 0}, {"size": 0}, {"size": 101}, {"q": "a" * 201}):
        invalid = await auth_client.get(VISITS_PATH, headers=headers, params=params)
        assert_error_envelope(invalid, 422, "validation_error")


async def test_ca17_filters_and_combinations(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-17: `doctor_id`, `patient_id`, `status`, `medication_id` y `AND`."""
    admin_id, headers = await admin_actor(db_conn)
    # FEAT-019: la anulacion la firma un usuario MEDICO, ya no un ADMIN.
    annuller_id, _ = await medico_actor(db_conn)
    first_doctor = await doctor_row(db_conn)
    second_doctor = await doctor_row(db_conn)
    first_patient = await patient_row(db_conn)
    second_patient = await patient_row(db_conn)
    medication_id = await create_medication_row(db_conn)

    first = await register_visit(
        db_conn,
        doctor_id=first_doctor,
        patient_id=first_patient,
        created_by=admin_id,
        medications=[(medication_id, None, None)],
    )
    second = await register_visit(
        db_conn,
        doctor_id=first_doctor,
        patient_id=second_patient,
        created_by=admin_id,
    )
    third = await register_visit(
        db_conn,
        doctor_id=second_doctor,
        patient_id=second_patient,
        created_by=admin_id,
    )
    await annul_visit_row(db_conn, third, annulled_by=annuller_id)

    by_doctor = await auth_client.get(
        VISITS_PATH, headers=headers, params={"doctor_id": first_doctor}
    )
    assert {item["id"] for item in by_doctor.json()["items"]} == {first, second}

    by_patient = await auth_client.get(
        VISITS_PATH, headers=headers, params={"patient_id": second_patient}
    )
    assert {item["id"] for item in by_patient.json()["items"]} == {second, third}

    by_status = await auth_client.get(VISITS_PATH, headers=headers, params={"status": "ANULADA"})
    assert [item["id"] for item in by_status.json()["items"]] == [third]
    assert by_status.json()["items"][0]["status"] == "ANULADA"

    invalid_status = await auth_client.get(
        VISITS_PATH, headers=headers, params={"status": "REGISTRADA_OTRA"}
    )
    assert "status" in {item["field"] for item in invalid_status.json()["errors"]}
    assert_error_envelope(invalid_status, 422, "validation_error")

    by_medication = await auth_client.get(
        VISITS_PATH, headers=headers, params={"medication_id": medication_id}
    )
    assert [item["id"] for item in by_medication.json()["items"]] == [first]

    combined = await auth_client.get(
        VISITS_PATH,
        headers=headers,
        params={
            "doctor_id": first_doctor,
            "patient_id": second_patient,
            "status": "REGISTRADA",
        },
    )
    assert [item["id"] for item in combined.json()["items"]] == [second]

    contradictory = await auth_client.get(
        VISITS_PATH,
        headers=headers,
        params={"doctor_id": second_doctor, "status": "REGISTRADA"},
    )
    assert contradictory.json()["items"] == []
    assert contradictory.json()["total"] == 0


async def test_ca18_q_searches_symptoms_and_recommendations(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-18: `q` es parcial y case-insensitive en sintomas o recomendaciones."""
    admin_id, headers = await admin_actor(db_conn)
    doctor_id = await doctor_row(db_conn)
    patient_id = await patient_row(db_conn)

    headache = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
        symptoms="Dolor de CABEZA fuerte",
    )
    rest = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
        symptoms="Control rutinario",
        recommendations="Reposo absoluto en casa",
    )
    percent = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
        symptoms="Herida con 50% de superficie",
    )

    by_symptom = await auth_client.get(VISITS_PATH, headers=headers, params={"q": "cabeza"})
    assert [item["id"] for item in by_symptom.json()["items"]] == [headache]

    by_recommendation = await auth_client.get(VISITS_PATH, headers=headers, params={"q": "REPOSO"})
    assert [item["id"] for item in by_recommendation.json()["items"]] == [rest]

    literal_percent = await auth_client.get(VISITS_PATH, headers=headers, params={"q": "%"})
    assert [item["id"] for item in literal_percent.json()["items"]] == [percent]

    blank = await auth_client.get(VISITS_PATH, headers=headers, params={"q": "   "})
    assert blank.json()["total"] == 3

    missing = await auth_client.get(VISITS_PATH, headers=headers, params={"q": "inexistente"})
    assert missing.json()["items"] == []
    assert missing.json()["total"] == 0


async def test_ca19_date_range_is_inclusive_and_normalized_to_utc(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-19: rango inclusivo sobre `visited_at`; `date_from > date_to` ⇒ 422."""
    admin_id, headers = await admin_actor(db_conn)
    doctor_id = await doctor_row(db_conn)
    patient_id = await patient_row(db_conn)

    first = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
        visited_at=FIRST_VISIT_AT,
    )
    middle = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
        visited_at=SECOND_VISIT_AT,
    )
    await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
        visited_at=THIRD_VISIT_AT,
    )

    bounded = await auth_client.get(
        VISITS_PATH,
        headers=headers,
        params={
            "date_from": FIRST_VISIT_AT.isoformat(),
            "date_to": SECOND_VISIT_AT.isoformat(),
            "sort": "visited_at",
        },
    )
    assert [item["id"] for item in bounded.json()["items"]] == [first, middle]

    only_first = await auth_client.get(
        VISITS_PATH,
        headers=headers,
        params={
            "date_from": FIRST_VISIT_AT.isoformat(),
            "date_to": FIRST_VISIT_AT.isoformat(),
        },
    )
    assert [item["id"] for item in only_first.json()["items"]] == [first]

    naive_santo_domingo = (FIRST_VISIT_AT - timedelta(hours=4)).replace(tzinfo=None)
    naive_query = await auth_client.get(
        VISITS_PATH,
        headers=headers,
        params={
            "date_from": naive_santo_domingo.isoformat(),
            "date_to": naive_santo_domingo.isoformat(),
        },
    )
    assert [item["id"] for item in naive_query.json()["items"]] == [first]

    inverted = await auth_client.get(
        VISITS_PATH,
        headers=headers,
        params={
            "date_from": THIRD_VISIT_AT.isoformat(),
            "date_to": FIRST_VISIT_AT.isoformat(),
        },
    )
    assert_field_error(
        inverted,
        422,
        "validation_error",
        "date_from",
        "La fecha inicial no puede ser mayor que la final",
    )


async def test_ca20_sort_catalog_and_default_descending_visited_at(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-20: orden por defecto `-visited_at`, catalogo cerrado y `sort` invalido."""
    admin_id, headers = await admin_actor(db_conn)
    doctor_id = await doctor_row(db_conn)
    patient_id = await patient_row(db_conn)

    first = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
        visited_at=FIRST_VISIT_AT,
    )
    second = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
        visited_at=SECOND_VISIT_AT,
    )
    third = await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
        visited_at=THIRD_VISIT_AT,
    )

    default_order = await auth_client.get(VISITS_PATH, headers=headers)
    assert [item["id"] for item in default_order.json()["items"]] == [third, second, first]

    explicit_desc = await auth_client.get(
        VISITS_PATH, headers=headers, params={"sort": "-visited_at"}
    )
    assert [item["id"] for item in explicit_desc.json()["items"]] == [third, second, first]

    ascending = await auth_client.get(VISITS_PATH, headers=headers, params={"sort": "visited_at"})
    assert [item["id"] for item in ascending.json()["items"]] == [first, second, third]

    by_id = await auth_client.get(VISITS_PATH, headers=headers, params={"sort": "-id"})
    assert [item["id"] for item in by_id.json()["items"]] == sorted(
        [first, second, third], reverse=True
    )

    for field in ("created_at", "status", "id", "-status"):
        accepted = await auth_client.get(VISITS_PATH, headers=headers, params={"sort": field})
        assert accepted.status_code == 200, (field, accepted.text)

    for invalid in ("doctor", "-", ""):
        rejected = await auth_client.get(VISITS_PATH, headers=headers, params={"sort": invalid})
        assert_error_envelope(rejected, 422, "validation_error")


async def test_ca21_medico_only_sees_own_visits(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-21: `MEDICO` solo ve sus visitas; `doctor_id` ⇒ 422; sin asociacion vacio."""
    admin_id, _ = await admin_actor(db_conn)
    own_doctor = await doctor_row(db_conn)
    other_doctor = await doctor_row(db_conn)
    patient_id = await patient_row(db_conn)

    own = await register_visit(
        db_conn,
        doctor_id=own_doctor,
        patient_id=patient_id,
        created_by=admin_id,
    )
    other = await register_visit(
        db_conn,
        doctor_id=other_doctor,
        patient_id=patient_id,
        created_by=admin_id,
    )

    _, own_headers = await medico_actor(db_conn, doctor_id=own_doctor)
    listing = await auth_client.get(
        VISITS_PATH, headers=own_headers, params={"patient_id": patient_id}
    )
    assert listing.status_code == 200, listing.text
    assert [item["id"] for item in listing.json()["items"]] == [own]
    assert listing.json()["total"] == 1

    forbidden_filter = await auth_client.get(
        VISITS_PATH, headers=own_headers, params={"doctor_id": other_doctor}
    )
    assert_field_error(
        forbidden_filter, 422, "validation_error", "doctor_id", DOCTOR_FILTER_FORBIDDEN_MESSAGE
    )

    _, orphan_headers = await medico_actor(db_conn)
    empty = await auth_client.get(VISITS_PATH, headers=orphan_headers)
    assert empty.status_code == 200, empty.text
    assert empty.json() == {"items": [], "total": 0, "page": 1, "size": 20, "pages": 0}

    visible = await auth_client.get(f"{VISITS_PATH}/{other}", headers=own_headers)
    assert_error_envelope(visible, 404, "visit_not_found")


async def test_ca22_list_item_shape_is_exactly_d8(
    auth_client: AsyncClient, db_conn: AsyncConnection
) -> None:
    """CA-22: cada item tiene exactamente las claves de la forma resumida (D-8)."""
    admin_id, headers = await admin_actor(db_conn)
    doctor_id = await doctor_row(db_conn)
    patient_id = await patient_row(db_conn)
    medication_id = await create_medication_row(db_conn)
    await register_visit(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=admin_id,
        recommendations="Reposo",
        medications=[(medication_id, 2, "Cada 8 horas")],
    )

    listing = await auth_client.get(VISITS_PATH, headers=headers)
    assert listing.status_code == 200, listing.text
    item = listing.json()["items"][0]

    assert set(item) == LIST_ITEM_KEYS
    assert set(item["doctor"]) == DOCTOR_REF_KEYS
    assert set(item["patient"]) == PATIENT_REF_KEYS
    assert item["doctor"]["id"] == doctor_id
    assert item["patient"]["id"] == patient_id
    assert item["created_by"] == admin_id
    assert item["status"] == "REGISTRADA"
    assert "medications" not in item
    assert "recommendations" not in item
    assert "created_at" not in item
