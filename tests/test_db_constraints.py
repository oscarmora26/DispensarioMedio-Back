"""Tests de integracion de constraints e integridad referencial.

Cada test corre dentro de la transaccion del fixture `db_conn`, que se revierte
al finalizar. Los casos invalidos usan SAVEPOINTs para poder seguir usando la
conexion despues de la violacion.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.models.audit_log import AuditLog
from tests.db_support import (
    assert_database_error,
    assert_integrity_error,
    create_audit_log,
    create_brand,
    create_doctor,
    create_drug_type,
    create_location,
    create_medication,
    create_patient,
    create_user,
    create_visit,
    create_visit_medication,
    fetch_all,
    fetch_scalar,
    get_role_id,
    insert_id,
    insert_visit_raw,
    unique_cedula,
)
from tests.test_models_metadata import VERSIONS_DIR, load_migration

SEED_MODULE = load_migration(VERSIONS_DIR / "42de9c3ddd3a_seed_roles.py")


async def test_ck_roles_name_allowed_rejects_unknown_role(db_conn: AsyncConnection) -> None:
    """Solo ADMIN y MEDICO; `AUDITOR` (plan #17) tambien es rechazado por diseno."""
    exc = await assert_integrity_error(
        db_conn, "INSERT INTO roles (name, description) VALUES ('SUPERVISOR', 'x')"
    )
    auditor = await assert_integrity_error(
        db_conn, "INSERT INTO roles (name, description) VALUES ('AUDITOR', 'x')"
    )

    assert "ck_roles_name_allowed" in str(exc.orig)
    assert "ck_roles_name_allowed" in str(auditor.orig)


@pytest.mark.parametrize(
    "cedula",
    ["1234567890", "123456789012", "1234567890A", "", "12345 67890", " 1234567890"],
    ids=["diez_digitos", "doce_digitos", "letras", "vacio", "espacio_interno", "espacio_inicial"],
)
async def test_ck_doctors_cedula_format_rejects_invalid_values(
    db_conn: AsyncConnection, cedula: str
) -> None:
    sql = """
        INSERT INTO doctors (name, cedula, lab_shift, specialty)
        VALUES ('Doctor invalido', :cedula, 'Matutino', 'General')
    """
    if len(cedula) > 11:
        # El typmod VARCHAR(11) rechaza el valor antes de evaluar el CHECK.
        exc = await assert_database_error(db_conn, sql, {"cedula": cedula})
        assert "value too long" in str(exc.orig)
    else:
        exc = await assert_integrity_error(db_conn, sql, {"cedula": cedula})
        assert "ck_doctors_cedula_format" in str(exc.orig)


async def test_ck_doctors_cedula_format_accepts_eleven_digits(db_conn: AsyncConnection) -> None:
    doctor_id = await create_doctor(db_conn, cedula="12345678901")
    stored = await fetch_scalar(
        db_conn, "SELECT cedula FROM doctors WHERE id = :id", {"id": doctor_id}
    )

    assert stored == "12345678901"


@pytest.mark.parametrize(
    "cedula",
    ["1234567890", "123456789012", "1234567890A", "", " 1234567890"],
    ids=["diez_digitos", "doce_digitos", "letras", "vacio", "espacio_inicial"],
)
async def test_ck_patients_cedula_format_rejects_invalid_values(
    db_conn: AsyncConnection, cedula: str
) -> None:
    sql = """
        INSERT INTO patients (name, cedula, patient_type)
        VALUES ('Paciente invalido', :cedula, 'EMPLEADO')
    """
    if len(cedula) > 11:
        exc = await assert_database_error(db_conn, sql, {"cedula": cedula})
        assert "value too long" in str(exc.orig)
    else:
        exc = await assert_integrity_error(db_conn, sql, {"cedula": cedula})
        assert "ck_patients_cedula_format" in str(exc.orig)


async def test_ck_patients_cedula_format_accepts_eleven_digits(db_conn: AsyncConnection) -> None:
    patient_id = await create_patient(db_conn, cedula="10987654321")
    stored = await fetch_scalar(
        db_conn, "SELECT cedula FROM patients WHERE id = :id", {"id": patient_id}
    )

    assert stored == "10987654321"


async def test_ck_patients_patient_type_allowed(db_conn: AsyncConnection) -> None:
    exc = await assert_integrity_error(
        db_conn,
        """
        INSERT INTO patients (name, cedula, patient_type)
        VALUES ('Visitante', :cedula, 'VISITANTE')
        """,
        {"cedula": unique_cedula()},
    )

    assert "ck_patients_patient_type_allowed" in str(exc.orig)


@pytest.mark.parametrize("carnet", [None, "", "   "], ids=["null", "vacio", "espacios"])
async def test_ck_patients_carnet_required_for_student(
    db_conn: AsyncConnection, carnet: str | None
) -> None:
    exc = await assert_integrity_error(
        db_conn,
        """
        INSERT INTO patients (name, cedula, patient_type, carnet)
        VALUES ('Estudiante sin carnet', :cedula, 'ESTUDIANTE', :carnet)
        """,
        {"cedula": unique_cedula(), "carnet": carnet},
    )

    assert "ck_patients_carnet_required_for_student" in str(exc.orig)


async def test_ck_patients_carnet_required_for_student_accepts_valid_carnet(
    db_conn: AsyncConnection,
) -> None:
    student_id = await create_patient(db_conn, patient_type="ESTUDIANTE", carnet="2021-0001")
    employee_id = await create_patient(db_conn, patient_type="EMPLEADO", carnet=None)

    student_carnet = await fetch_scalar(
        db_conn, "SELECT carnet FROM patients WHERE id = :id", {"id": student_id}
    )
    employee_carnet = await fetch_scalar(
        db_conn, "SELECT carnet FROM patients WHERE id = :id", {"id": employee_id}
    )

    assert student_carnet == "2021-0001"
    assert employee_carnet is None


async def test_carnet_with_internal_spaces_is_accepted(db_conn: AsyncConnection) -> None:
    """Plan #29: solo se prohibe vacio o solo espacios."""
    patient_id = await create_patient(db_conn, patient_type="ESTUDIANTE", carnet="2021 0001")
    stored = await fetch_scalar(
        db_conn, "SELECT carnet FROM patients WHERE id = :id", {"id": patient_id}
    )

    assert stored == "2021 0001"


async def test_uq_patients_carnet_is_case_insensitive_and_partial(
    db_conn: AsyncConnection,
) -> None:
    await create_patient(db_conn, patient_type="ESTUDIANTE", carnet="abc-1")

    duplicate = await assert_integrity_error(
        db_conn,
        """
        INSERT INTO patients (name, cedula, patient_type, carnet)
        VALUES ('Duplicado', :cedula, 'ESTUDIANTE', 'ABC-1')
        """,
        {"cedula": unique_cedula()},
    )

    assert "uq_patients_carnet" in str(duplicate.orig)

    # Varios NULL son validos: el indice es parcial.
    first = await create_patient(db_conn, patient_type="EMPLEADO", carnet=None)
    second = await create_patient(db_conn, patient_type="EMPLEADO", carnet=None)
    assert first != second


async def test_ck_visits_status_allowed(db_conn: AsyncConnection) -> None:
    doctor_id = await create_doctor(db_conn)
    patient_id = await create_patient(db_conn)
    user_id = await create_user(db_conn)

    exc = await assert_integrity_error(
        db_conn,
        """
        INSERT INTO visits (doctor_id, patient_id, visited_at, symptoms, status, created_by)
        VALUES (:doctor_id, :patient_id, now(), 'Sintomas', 'PENDIENTE', :created_by)
        """,
        {"doctor_id": doctor_id, "patient_id": patient_id, "created_by": user_id},
    )

    assert "ck_visits_status_allowed" in str(exc.orig) or (
        "ck_visits_annulment_consistency" in str(exc.orig)
    )


@pytest.mark.parametrize(
    ("status", "with_annulled_by", "with_annulled_at", "reason"),
    [
        ("REGISTRADA", True, False, None),
        ("REGISTRADA", False, True, None),
        ("REGISTRADA", False, False, "motivo"),
        ("ANULADA", False, True, "motivo"),
        ("ANULADA", True, False, "motivo"),
        ("ANULADA", True, True, ""),
        ("ANULADA", True, True, "   "),
    ],
    ids=[
        "registrada_con_annulled_by",
        "registrada_con_annulled_at",
        "registrada_con_motivo",
        "anulada_sin_annulled_by",
        "anulada_sin_annulled_at",
        "anulada_motivo_vacio",
        "anulada_motivo_en_blanco",
    ],
)
async def test_ck_visits_annulment_consistency_rejects_inconsistent_rows(
    db_conn: AsyncConnection,
    status: str,
    with_annulled_by: bool,
    with_annulled_at: bool,
    reason: str | None,
) -> None:
    """Regla 10: los tres campos deben estar presentes solo en el estado correcto."""
    doctor_id = await create_doctor(db_conn)
    patient_id = await create_patient(db_conn)
    user_id = await create_user(db_conn)

    exc = await assert_integrity_error(
        db_conn,
        """
        INSERT INTO visits (
            doctor_id, patient_id, visited_at, symptoms, status,
            created_by, annulled_by, annulled_at, annulment_reason
        )
        VALUES (
            :doctor_id, :patient_id, now(), 'Sintomas', :status,
            :created_by, :annulled_by, :annulled_at, :reason
        )
        """,
        {
            "doctor_id": doctor_id,
            "patient_id": patient_id,
            "created_by": user_id,
            "status": status,
            "annulled_by": user_id if with_annulled_by else None,
            "annulled_at": datetime.now(UTC) if with_annulled_at else None,
            "reason": reason,
        },
    )

    assert "ck_visits_annulment_consistency" in str(exc.orig)


async def test_ck_visits_annulment_consistency_rejects_null_reason_for_annulled(
    db_conn: AsyncConnection,
) -> None:
    """Regla 10 y plan #15: ANULADA con `annulment_reason` NULL debe fallar.

    La formula del CHECK usa `btrim(annulment_reason) <> ''`; con NULL esa
    comparacion evalua NULL, y PostgreSQL acepta la fila. Falta `IS NOT NULL`.
    """
    doctor_id = await create_doctor(db_conn)
    patient_id = await create_patient(db_conn)
    user_id = await create_user(db_conn)

    exc = await assert_integrity_error(
        db_conn,
        """
        INSERT INTO visits (
            doctor_id, patient_id, visited_at, symptoms, status,
            created_by, annulled_by, annulled_at, annulment_reason
        )
        VALUES (
            :doctor_id, :patient_id, now(), 'Sintomas', 'ANULADA',
            :created_by, :annulled_by, now(), NULL
        )
        """,
        {
            "doctor_id": doctor_id,
            "patient_id": patient_id,
            "created_by": user_id,
            "annulled_by": user_id,
        },
    )

    assert "ck_visits_annulment_consistency" in str(exc.orig)


async def test_ck_visits_annulment_consistency_accepts_valid_rows(
    db_conn: AsyncConnection,
) -> None:
    doctor_id = await create_doctor(db_conn)
    patient_id = await create_patient(db_conn)
    user_id = await create_user(db_conn)

    registered = await create_visit(
        db_conn, doctor_id=doctor_id, patient_id=patient_id, created_by=user_id
    )
    annulled = await insert_visit_raw(
        db_conn,
        doctor_id=doctor_id,
        patient_id=patient_id,
        created_by=user_id,
        status="ANULADA",
        annulled_by=user_id,
        annulled_at=datetime.now(UTC),
        annulment_reason="Error de digitacion",
    )

    statuses = await fetch_all(
        db_conn,
        "SELECT status FROM visits WHERE id = ANY(:ids) ORDER BY id",
        {"ids": [registered, annulled]},
    )

    assert [row[0] for row in statuses] == ["REGISTRADA", "ANULADA"]


@pytest.mark.parametrize(
    "quantity",
    [Decimal("0"), Decimal("-1.5"), Decimal("0.004"), Decimal("-0.01")],
    ids=["cero", "negativo", "redondea_a_cero", "negativo_pequeno"],
)
async def test_ck_visit_medications_quantity_positive_rejects_invalid(
    db_conn: AsyncConnection, quantity: Decimal
) -> None:
    visit_id = await create_visit(db_conn)
    medication_id = await create_medication(db_conn)

    exc = await assert_integrity_error(
        db_conn,
        """
        INSERT INTO visit_medications (visit_id, medication_id, quantity)
        VALUES (:visit_id, :medication_id, :quantity)
        """,
        {"visit_id": visit_id, "medication_id": medication_id, "quantity": quantity},
    )

    assert "ck_visit_medications_quantity_positive" in str(exc.orig)


@pytest.mark.parametrize(
    "quantity",
    [Decimal("0.01"), Decimal("12.50"), None],
    ids=["minimo", "decimal", "null"],
)
async def test_ck_visit_medications_quantity_positive_accepts_valid(
    db_conn: AsyncConnection, quantity: Decimal | None
) -> None:
    visit_id = await create_visit(db_conn)
    medication_id = await create_medication(db_conn)

    detail_id = await insert_id(
        db_conn,
        """
        INSERT INTO visit_medications (visit_id, medication_id, quantity)
        VALUES (:visit_id, :medication_id, :quantity)
        RETURNING id
        """,
        {"visit_id": visit_id, "medication_id": medication_id, "quantity": quantity},
    )
    stored = await fetch_scalar(
        db_conn,
        "SELECT quantity FROM visit_medications WHERE id = :id",
        {"id": detail_id},
    )

    assert stored == quantity


@pytest.mark.parametrize("action", ["CREATE", "UPDATE", "DEACTIVATE", "ANNUL", "LOGIN"])
async def test_ck_audit_log_action_allowed_accepts_allowed_actions(
    db_conn: AsyncConnection, action: str
) -> None:
    audit_id = await create_audit_log(db_conn, action=action)
    stored = await fetch_scalar(
        db_conn, "SELECT action FROM audit_log WHERE id = :id", {"id": audit_id}
    )

    assert stored == action


async def test_ck_audit_log_action_allowed_rejects_delete(db_conn: AsyncConnection) -> None:
    exc = await assert_integrity_error(
        db_conn,
        """
        INSERT INTO audit_log (action, entity, entity_id)
        VALUES ('DELETE', 'users', 1)
        """,
    )

    assert "ck_audit_log_action_allowed" in str(exc.orig)


async def test_uq_visit_medications_rejects_duplicate_pair(
    db_conn: AsyncConnection,
) -> None:
    visit_id = await create_visit(db_conn)
    medication_id = await create_medication(db_conn)

    await create_visit_medication(
        db_conn, visit_id=visit_id, medication_id=medication_id, quantity=Decimal("1.00")
    )
    exc = await assert_integrity_error(
        db_conn,
        """
        INSERT INTO visit_medications (visit_id, medication_id, quantity)
        VALUES (:visit_id, :medication_id, 2.00)
        """,
        {"visit_id": visit_id, "medication_id": medication_id},
    )

    assert "uq_visit_medications" in str(exc.orig)


async def test_uq_locations_position_rejects_same_position(db_conn: AsyncConnection) -> None:
    await insert_id(
        db_conn,
        """
        INSERT INTO locations (description, shelf, section, cell)
        VALUES ('Estante A', 'A', '1', '1')
        RETURNING id
        """,
    )
    exc = await assert_integrity_error(
        db_conn,
        """
        INSERT INTO locations (description, shelf, section, cell)
        VALUES ('Estante A bis', 'a', '1', '1')
        """,
    )

    assert "uq_locations_position" in str(exc.orig)


async def test_uq_medications_identity_rejects_same_description_brand_and_dose(
    db_conn: AsyncConnection,
) -> None:
    drug_type_id = await create_drug_type(db_conn)
    brand_id = await create_brand(db_conn)
    location_id = await create_location(db_conn)
    await create_medication(
        db_conn,
        description="Ibuprofeno",
        drug_type_id=drug_type_id,
        brand_id=brand_id,
        location_id=location_id,
        dose="400 mg",
    )

    duplicate = await assert_integrity_error(
        db_conn,
        """
        INSERT INTO medications (description, drug_type_id, brand_id, location_id, dose)
        VALUES ('IBUPROFENO', :drug_type_id, :brand_id, :location_id, '400 mg')
        """,
        {
            "drug_type_id": drug_type_id,
            "brand_id": brand_id,
            "location_id": location_id,
        },
    )
    assert "uq_medications_identity" in str(duplicate.orig)

    other_dose = await create_medication(
        db_conn,
        description="Ibuprofeno",
        drug_type_id=drug_type_id,
        brand_id=brand_id,
        location_id=location_id,
        dose="600 mg",
    )
    assert other_dose


async def test_uq_users_username_is_case_insensitive(db_conn: AsyncConnection) -> None:
    await create_user(db_conn, username="Oscar")

    exc = await assert_integrity_error(
        db_conn,
        """
        INSERT INTO users (username, email, password_hash, role_id)
        VALUES ('oscar', :email, 'x', :role_id)
        """,
        {"email": f"{unique_cedula()}@example.test", "role_id": await get_role_id(db_conn)},
    )

    assert "uq_users_username" in str(exc.orig)


async def test_uq_users_email_is_case_insensitive(db_conn: AsyncConnection) -> None:
    await create_user(db_conn, email="Oscar@Example.test")

    exc = await assert_integrity_error(
        db_conn,
        """
        INSERT INTO users (username, email, password_hash, role_id)
        VALUES (:username, 'oscar@example.test', 'x', :role_id)
        """,
        {"username": f"otro_{unique_cedula()}", "role_id": await get_role_id(db_conn)},
    )

    assert "uq_users_email" in str(exc.orig)


async def test_uq_users_doctor_is_unique_but_allows_multiple_nulls(
    db_conn: AsyncConnection,
) -> None:
    doctor_id = await create_doctor(db_conn)
    await create_user(db_conn, doctor_id=doctor_id)

    exc = await assert_integrity_error(
        db_conn,
        """
        INSERT INTO users (username, email, password_hash, role_id, doctor_id)
        VALUES (:username, :email, 'x', :role_id, :doctor_id)
        """,
        {
            "username": f"otro_{unique_cedula()}",
            "email": f"{unique_cedula()}@example.test",
            "role_id": await get_role_id(db_conn),
            "doctor_id": doctor_id,
        },
    )
    assert "uq_users_doctor" in str(exc.orig)

    first = await create_user(db_conn, doctor_id=None)
    second = await create_user(db_conn, doctor_id=None)
    assert first != second


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("doctors", "cedula", "12345678901"),
        ("patients", "cedula", "12345678901"),
        ("drug_types", "description", "Analgesico"),
        ("brands", "description", "Genfar"),
        ("roles", "name", "ADMIN"),
    ],
)
async def test_unique_constraints_reject_duplicates(
    db_conn: AsyncConnection, table: str, column: str, value: str
) -> None:
    """`uq_doctors_cedula`, `uq_patients_cedula`, catalogos y roles."""
    if table == "doctors":
        await create_doctor(db_conn, cedula=value)
        sql = """
            INSERT INTO doctors (name, cedula, lab_shift, specialty)
            VALUES ('Otro', :value, 'Matutino', 'General')
        """
    elif table == "patients":
        await create_patient(db_conn, cedula=value)
        sql = """
            INSERT INTO patients (name, cedula, patient_type)
            VALUES ('Otro', :value, 'EMPLEADO')
        """
    elif table == "drug_types":
        await insert_id(
            db_conn,
            "INSERT INTO drug_types (description) VALUES (:value) RETURNING id",
            {"value": value},
        )
        sql = "INSERT INTO drug_types (description) VALUES (:value)"
    elif table == "brands":
        await insert_id(
            db_conn,
            "INSERT INTO brands (description) VALUES (:value) RETURNING id",
            {"value": value},
        )
        sql = "INSERT INTO brands (description) VALUES (:value)"
    else:
        sql = "INSERT INTO roles (name) VALUES (:value)"

    exc = await assert_integrity_error(db_conn, sql, {"value": value})

    assert f"uq_{table}_{column}" in str(exc.orig)


async def test_uq_drug_type_and_brand_are_case_insensitive(db_conn: AsyncConnection) -> None:
    await insert_id(
        db_conn, "INSERT INTO drug_types (description) VALUES ('Analgesico') RETURNING id"
    )
    await insert_id(db_conn, "INSERT INTO brands (description) VALUES ('Genfar') RETURNING id")

    drug_type = await assert_integrity_error(
        db_conn, "INSERT INTO drug_types (description) VALUES ('ANALGESICO')"
    )
    brand = await assert_integrity_error(
        db_conn, "INSERT INTO brands (description) VALUES ('GENFAR')"
    )

    assert "uq_drug_types_description" in str(drug_type.orig)
    assert "uq_brands_description" in str(brand.orig)


async def test_fk_restrict_prevents_deleting_referenced_role(
    db_conn: AsyncConnection,
) -> None:
    role_id = await get_role_id(db_conn)
    await create_user(db_conn, role_id=role_id)

    exc = await assert_integrity_error(
        db_conn, "DELETE FROM roles WHERE id = :role_id", {"role_id": role_id}
    )

    assert "fk_users_role_id_roles" in str(exc.orig)


async def test_fk_restrict_prevents_deleting_doctor_with_user(
    db_conn: AsyncConnection,
) -> None:
    doctor_id = await create_doctor(db_conn)
    await create_user(db_conn, doctor_id=doctor_id)

    exc = await assert_integrity_error(
        db_conn, "DELETE FROM doctors WHERE id = :id", {"id": doctor_id}
    )

    assert "fk_users_doctor_id_doctors" in str(exc.orig)


async def test_fk_restrict_prevents_deleting_doctor_with_visits(
    db_conn: AsyncConnection,
) -> None:
    doctor_id = await create_doctor(db_conn)
    await create_visit(db_conn, doctor_id=doctor_id)

    exc = await assert_integrity_error(
        db_conn, "DELETE FROM doctors WHERE id = :id", {"id": doctor_id}
    )

    assert "fk_visits_doctor_id_doctors" in str(exc.orig)


async def test_fk_restrict_prevents_deleting_patient_with_visits(
    db_conn: AsyncConnection,
) -> None:
    patient_id = await create_patient(db_conn)
    await create_visit(db_conn, patient_id=patient_id)

    exc = await assert_integrity_error(
        db_conn, "DELETE FROM patients WHERE id = :id", {"id": patient_id}
    )

    assert "fk_visits_patient_id_patients" in str(exc.orig)


async def test_fk_restrict_prevents_deleting_medication_with_visit_medications(
    db_conn: AsyncConnection,
) -> None:
    medication_id = await create_medication(db_conn)
    await create_visit_medication(db_conn, medication_id=medication_id)

    exc = await assert_integrity_error(
        db_conn, "DELETE FROM medications WHERE id = :id", {"id": medication_id}
    )

    assert "fk_visit_medications_medication_id_medications" in str(exc.orig)


async def test_fk_restrict_prevents_deleting_users_with_visits(
    db_conn: AsyncConnection,
) -> None:
    creator_id = await create_user(db_conn)
    annuller_id = await create_user(db_conn)
    visit_id = await insert_visit_raw(
        db_conn,
        doctor_id=await create_doctor(db_conn),
        patient_id=await create_patient(db_conn),
        created_by=creator_id,
        status="ANULADA",
        annulled_by=annuller_id,
        annulled_at=datetime.now(UTC),
        annulment_reason="Duplicada",
    )

    creator_exc = await assert_integrity_error(
        db_conn, "DELETE FROM users WHERE id = :id", {"id": creator_id}
    )
    annuller_exc = await assert_integrity_error(
        db_conn, "DELETE FROM users WHERE id = :id", {"id": annuller_id}
    )

    assert "fk_visits_created_by_users" in str(creator_exc.orig)
    assert "fk_visits_annulled_by_users" in str(annuller_exc.orig)
    assert visit_id


async def test_fk_restrict_prevents_deleting_catalogs_with_medications(
    db_conn: AsyncConnection,
) -> None:
    drug_type_id = await create_drug_type(db_conn)
    brand_id = await create_brand(db_conn)
    location_id = await create_location(db_conn)
    await create_medication(
        db_conn, drug_type_id=drug_type_id, brand_id=brand_id, location_id=location_id
    )

    drug_type_exc = await assert_integrity_error(
        db_conn, "DELETE FROM drug_types WHERE id = :id", {"id": drug_type_id}
    )
    brand_exc = await assert_integrity_error(
        db_conn, "DELETE FROM brands WHERE id = :id", {"id": brand_id}
    )
    location_exc = await assert_integrity_error(
        db_conn, "DELETE FROM locations WHERE id = :id", {"id": location_id}
    )

    assert "fk_medications_drug_type_id_drug_types" in str(drug_type_exc.orig)
    assert "fk_medications_brand_id_brands" in str(brand_exc.orig)
    assert "fk_medications_location_id_locations" in str(location_exc.orig)


async def test_fk_cascade_deletes_visit_medications_and_keeps_medications(
    db_conn: AsyncConnection,
) -> None:
    visit_id = await create_visit(db_conn)
    medication_id = await create_medication(db_conn)
    await create_visit_medication(
        db_conn, visit_id=visit_id, medication_id=medication_id, quantity=Decimal("1.00")
    )

    await db_conn.execute(text("DELETE FROM visits WHERE id = :id"), {"id": visit_id})

    details = await fetch_scalar(
        db_conn,
        "SELECT count(*) FROM visit_medications WHERE visit_id = :id",
        {"id": visit_id},
    )
    medications = await fetch_scalar(
        db_conn, "SELECT count(*) FROM medications WHERE id = :id", {"id": medication_id}
    )

    assert details == 0
    assert medications == 1


async def test_fk_set_null_keeps_audit_row_when_user_is_deleted(
    db_conn: AsyncConnection,
) -> None:
    user_id = await create_user(db_conn)
    audit_id = await create_audit_log(db_conn, user_id=user_id)

    await db_conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
    stored_user_id = await fetch_scalar(
        db_conn, "SELECT user_id FROM audit_log WHERE id = :id", {"id": audit_id}
    )

    assert stored_user_id is None


async def test_created_at_updated_at_and_boolean_defaults_are_filled_by_database(
    db_conn: AsyncConnection,
) -> None:
    doctor_id = await insert_id(
        db_conn,
        """
        INSERT INTO doctors (name, cedula, lab_shift, specialty)
        VALUES ('Doctor defaults', :cedula, 'Matutino', 'General')
        RETURNING id
        """,
        {"cedula": unique_cedula()},
    )
    row = (
        await fetch_all(
            db_conn,
            """
            SELECT created_at, updated_at, is_active FROM doctors WHERE id = :id
            """,
            {"id": doctor_id},
        )
    )[0]

    assert row[0] is not None
    assert row[1] is not None
    assert row[2] is True


async def test_seed_roles_is_idempotent_in_transaction(db_conn: AsyncConnection) -> None:
    """CA-17/plan #23: el seed repetido no duplica roles ni cambia sus ids."""
    before = await fetch_all(db_conn, "SELECT name, id FROM roles ORDER BY name")

    await db_conn.execute(text(SEED_MODULE.SEED_ROLES_SQL))
    await db_conn.execute(text(SEED_MODULE.SEED_ROLES_SQL))

    after = await fetch_all(db_conn, "SELECT name, id FROM roles ORDER BY name")

    assert [row[0] for row in after] == ["ADMIN", "MEDICO"]
    assert after == before


async def test_unseed_sql_keeps_roles_in_use(db_conn: AsyncConnection) -> None:
    """R-6: el downgrade del seed no borra un rol referenciado por usuarios.

    La BD de desarrollo puede contener cuentas permanentes que usan ADMIN y/o
    MEDICO; el test comprueba la invariante (todo rol en uso sobrevive) en lugar
    de asumir que la base esta vacia.
    """
    role_id = await get_role_id(db_conn)
    created_user = await create_user(db_conn, role_id=role_id)
    roles_in_use = {
        row[0]
        for row in await fetch_all(
            db_conn,
            """
            SELECT DISTINCT r.name FROM roles r
            JOIN users u ON u.role_id = r.id
            """,
        )
    }

    await db_conn.execute(text(SEED_MODULE.UNSEED_ROLES_SQL))

    remaining = {row[0] for row in await fetch_all(db_conn, "SELECT name FROM roles ORDER BY name")}
    assert roles_in_use <= remaining
    assert "ADMIN" in remaining
    assert (
        await fetch_scalar(
            db_conn,
            "SELECT count(*) FROM users WHERE id = :id",
            {"id": created_user},
        )
        == 1
    )


async def test_audit_log_ip_is_returned_as_str(db_conn: AsyncConnection) -> None:
    """Contrato declarado en la spec: `audit_log.ip` es `str | None` en Python.

    La spec exige `native_inet_types=False` para no depender del default del
    dialecto. SQLAlchemy 2.1.5 devuelve `ipaddress.IPv4Address`, no `str`.
    """
    async with AsyncSession(
        bind=db_conn,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        audit = AuditLog(
            user_id=None,
            action="LOGIN",
            entity="users",
            entity_id=1,
            ip="192.168.1.10",
        )
        session.add(audit)
        await session.flush()
        await session.refresh(audit)

        assert isinstance(audit.ip, str), (
            "audit_log.ip debe respetar el contrato str | None, "
            f"pero el ORM devolvio {type(audit.ip).__name__}: {audit.ip!r}"
        )


def test_app_engine_disables_native_inet_types() -> None:
    """R-13: el engine de la app configura `native_inet_types=False`."""
    from app.db.session import engine

    assert engine.dialect._native_inet_types is False


def test_audit_log_ip_annotation_claims_str() -> None:
    """La anotacion del modelo promete `str | None`; se contrasta con el runtime."""
    annotations = inspect.get_annotations(AuditLog)

    assert annotations["ip"] == "Mapped[str | None]"
