"""Tests unitarios de la metadata de los modelos (CA-1 a CA-4 y CA-6).

No tocan la base de datos: comparan `Base.metadata` y los archivos de
`alembic/versions/` con el contrato de `docs/features/FEAT-003.md`.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Numeric,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    inspect,
    update,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import CITEXT, INET, JSONB
from sqlalchemy.orm import configure_mappers

from app import models
from app.db.base import Base

ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "app" / "models"
VERSIONS_DIR = ROOT / "alembic" / "versions"

EXPECTED_TABLES = {
    "audit_log",
    "brands",
    "doctors",
    "drug_types",
    "locations",
    "medications",
    "patients",
    "roles",
    "users",
    "visit_medications",
    "visits",
}

EXPECTED_MODEL_FILES = {
    "__init__.py",
    "mixins.py",
    "audit_log.py",
    "brand.py",
    "doctor.py",
    "drug_type.py",
    "location.py",
    "medication.py",
    "patient.py",
    "role.py",
    "user.py",
    "visit.py",
    "visit_medication.py",
}

EXPECTED_PKS = {f"pk_{table}" for table in EXPECTED_TABLES}

EXPECTED_UQS = {
    "uq_roles_name",
    "uq_users_username",
    "uq_users_email",
    "uq_users_doctor",
    "uq_doctors_cedula",
    "uq_drug_types_description",
    "uq_brands_description",
    "uq_locations_position",
    "uq_medications_identity",
    "uq_patients_cedula",
    "uq_visit_medications",
}

EXPECTED_CKS = {
    "ck_roles_name_allowed",
    "ck_doctors_cedula_format",
    "ck_patients_cedula_format",
    "ck_patients_patient_type_allowed",
    "ck_patients_carnet_required_for_student",
    "ck_visits_status_allowed",
    "ck_visits_annulment_consistency",
    "ck_visit_medications_quantity_positive",
    "ck_audit_log_action_allowed",
}

EXPECTED_FKS = {
    "fk_users_role_id_roles": ("users", "roles.id", "RESTRICT"),
    "fk_users_doctor_id_doctors": ("users", "doctors.id", "RESTRICT"),
    "fk_medications_drug_type_id_drug_types": ("medications", "drug_types.id", "RESTRICT"),
    "fk_medications_brand_id_brands": ("medications", "brands.id", "RESTRICT"),
    "fk_medications_location_id_locations": ("medications", "locations.id", "RESTRICT"),
    "fk_visits_doctor_id_doctors": ("visits", "doctors.id", "RESTRICT"),
    "fk_visits_patient_id_patients": ("visits", "patients.id", "RESTRICT"),
    "fk_visits_created_by_users": ("visits", "users.id", "RESTRICT"),
    "fk_visits_annulled_by_users": ("visits", "users.id", "RESTRICT"),
    "fk_visit_medications_visit_id_visits": ("visit_medications", "visits.id", "CASCADE"),
    "fk_visit_medications_medication_id_medications": (
        "visit_medications",
        "medications.id",
        "RESTRICT",
    ),
    "fk_audit_log_user_id_users": ("audit_log", "users.id", "SET NULL"),
}

EXPECTED_FK_INDEXES = {
    "ix_users_role_id",
    "ix_medications_drug_type_id",
    "ix_medications_brand_id",
    "ix_medications_location_id",
    "ix_visit_medications_visit_id",
    "ix_visit_medications_medication_id",
    "ix_visits_created_by",
    "ix_visits_annulled_by",
}

EXPECTED_VISIT_INDEXES = {
    "ix_visits_doctor_visited",
    "ix_visits_patient_visited",
    "ix_visits_visited",
    "ix_visits_status_visited",
}

EXPECTED_TRGM_INDEXES = {
    "ix_doctors_name_trgm",
    "ix_patients_name_trgm",
    "ix_medications_description_trgm",
}

EXPECTED_NON_PK_INDEXES = (
    EXPECTED_UQS
    | {"uq_patients_carnet"}
    | EXPECTED_FK_INDEXES
    | EXPECTED_VISIT_INDEXES
    | EXPECTED_TRGM_INDEXES
)

EXPECTED_REVISION_ORDER = [
    "extensions",
    "auth_schema",
    "catalog_schema",
    "clinical_schema",
    "audit_schema",
    "seed_roles",
]

EXPECTED_RELATIONSHIPS = {
    ("Role", "users"): False,
    ("User", "role"): True,
    ("User", "doctor"): True,
    ("Doctor", "user"): True,
    ("Doctor", "visits"): False,
    ("DrugType", "medications"): False,
    ("Brand", "medications"): False,
    ("Location", "medications"): False,
    ("Medication", "drug_type"): True,
    ("Medication", "brand"): True,
    ("Medication", "location"): True,
    ("Medication", "visit_medications"): False,
    ("Patient", "visits"): False,
    ("Visit", "doctor"): True,
    ("Visit", "patient"): True,
    ("Visit", "created_by_user"): True,
    ("Visit", "annulled_by_user"): True,
    ("Visit", "visit_medications"): False,
    ("VisitMedication", "visit"): True,
    ("VisitMedication", "medication"): True,
    ("AuditLog", "user"): True,
}

TABLES_WITH_UPDATED_AT = {
    "doctors",
    "users",
    "drug_types",
    "brands",
    "locations",
    "medications",
    "patients",
    "visits",
}


def column(table: str, name: str) -> Any:
    """Devuelve la columna `name` de la tabla `table` en la metadata."""
    return Base.metadata.tables[table].c[name]


def all_constraints() -> list[Any]:
    """Todas las constraints declaradas en la metadata."""
    return [
        constraint for table in Base.metadata.tables.values() for constraint in table.constraints
    ]


def load_migration(path: Path) -> ModuleType:
    """Carga una revision de Alembic sin importarla por nombre de paquete."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migration_modules() -> dict[str, ModuleType]:
    """Carga todas las revisiones y las indexa por id de revision."""
    return {
        module.revision: module
        for module in (load_migration(path) for path in sorted(VERSIONS_DIR.glob("*.py")))
    }


def test_ca01_metadata_contains_exactly_the_eleven_domain_tables() -> None:
    """CA-1: las 11 tablas del dominio, con nombres exactos y sin esquema."""
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    assert sorted(Base.metadata.tables) == [
        "audit_log",
        "brands",
        "doctors",
        "drug_types",
        "locations",
        "medications",
        "patients",
        "roles",
        "users",
        "visit_medications",
        "visits",
    ]
    assert all(table.schema is None for table in Base.metadata.tables.values())


def test_ca02_mappers_configure_without_ambiguity() -> None:
    """CA-2: todas las relaciones resuelven sin ambiguedad de claves foraneas."""
    configure_mappers()

    for mapper in Base.registry.mappers:
        assert mapper.relationships is not None


def test_ca03_no_create_all_in_app_and_no_enum_in_models() -> None:
    """CA-3: prohibido `create_all()` y los ENUM nativos."""
    create_all_files = [
        str(path)
        for path in (ROOT / "app").rglob("*.py")
        if "create_all" in path.read_text(encoding="utf-8")
    ]
    enum_files = [
        str(path)
        for path in MODELS_DIR.rglob("*.py")
        if "Enum(" in path.read_text(encoding="utf-8")
        or "sa.Enum(" in path.read_text(encoding="utf-8")
    ]

    assert create_all_files == []
    assert enum_files == []
    assert "create_all" not in (ROOT / "alembic" / "env.py").read_text(encoding="utf-8")


def test_ca04_models_package_has_expected_files_and_exports() -> None:
    """CA-4: el listado de `app/models/` y el `__init__` que puebla la metadata."""
    model_files = {path.name for path in MODELS_DIR.glob("*.py")}

    assert model_files == EXPECTED_MODEL_FILES
    assert set(models.__all__) == {
        "AuditLog",
        "Brand",
        "Doctor",
        "DrugType",
        "Location",
        "Medication",
        "Patient",
        "Role",
        "User",
        "Visit",
        "VisitMedication",
    }
    for name in models.__all__:
        assert getattr(models, name).__table__.name in Base.metadata.tables


def test_metadata_column_types_match_the_spec() -> None:
    """Plan #2: tipos declarados en los modelos."""
    quantity = column("visit_medications", "quantity").type
    assert isinstance(quantity, Numeric)
    assert (quantity.precision, quantity.scale) == (10, 2)

    assert isinstance(column("audit_log", "old_data").type, JSONB)
    assert isinstance(column("audit_log", "new_data").type, JSONB)
    assert isinstance(column("audit_log", "ip").type, INET)

    citext_columns = {
        ("users", "username"),
        ("users", "email"),
        ("patients", "carnet"),
        ("drug_types", "description"),
        ("brands", "description"),
        ("locations", "description"),
        ("locations", "shelf"),
        ("locations", "section"),
        ("locations", "cell"),
        ("medications", "description"),
        ("medications", "dose"),
    }
    for table_name, column_name in citext_columns:
        assert isinstance(column(table_name, column_name).type, CITEXT), (
            table_name,
            column_name,
        )

    varchar_lengths = {
        ("roles", "name"): 20,
        ("roles", "description"): 100,
        ("doctors", "name"): 150,
        ("doctors", "cedula"): 11,
        ("doctors", "lab_shift"): 50,
        ("doctors", "specialty"): 100,
        ("users", "password_hash"): 255,
        ("patients", "name"): 150,
        ("patients", "cedula"): 11,
        ("patients", "patient_type"): 20,
        ("visits", "symptoms"): 2000,
        ("visits", "recommendations"): 2000,
        ("visits", "status"): 20,
        ("visits", "annulment_reason"): 500,
        ("visit_medications", "instructions"): 255,
        ("audit_log", "action"): 30,
        ("audit_log", "entity"): 50,
    }
    for (table_name, column_name), length in varchar_lengths.items():
        column_type = column(table_name, column_name).type
        assert isinstance(column_type, String), (table_name, column_name)
        assert column_type.length == length, (table_name, column_name)

    for table_name in (
        "doctors",
        "users",
        "drug_types",
        "brands",
        "locations",
        "medications",
        "patients",
    ):
        assert isinstance(column(table_name, "is_active").type, Boolean), table_name

    timestamp_columns = {
        ("roles", "created_at"),
        ("doctors", "created_at"),
        ("doctors", "updated_at"),
        ("users", "last_login_at"),
        ("users", "created_at"),
        ("users", "updated_at"),
        ("drug_types", "created_at"),
        ("drug_types", "updated_at"),
        ("brands", "created_at"),
        ("brands", "updated_at"),
        ("locations", "created_at"),
        ("locations", "updated_at"),
        ("medications", "created_at"),
        ("medications", "updated_at"),
        ("patients", "created_at"),
        ("patients", "updated_at"),
        ("visits", "visited_at"),
        ("visits", "annulled_at"),
        ("visits", "created_at"),
        ("visits", "updated_at"),
        ("visit_medications", "created_at"),
        ("audit_log", "created_at"),
    }
    for table_name, column_name in timestamp_columns:
        column_type = column(table_name, column_name).type
        assert isinstance(column_type, DateTime), (table_name, column_name)
        assert column_type.timezone is True, (table_name, column_name)

    assert all(isinstance(column(table, "id").type, BigInteger) for table in EXPECTED_TABLES)
    bigint_columns = {
        ("users", "role_id"),
        ("users", "doctor_id"),
        ("medications", "drug_type_id"),
        ("medications", "brand_id"),
        ("medications", "location_id"),
        ("visits", "doctor_id"),
        ("visits", "patient_id"),
        ("visits", "created_by"),
        ("visits", "annulled_by"),
        ("visit_medications", "visit_id"),
        ("visit_medications", "medication_id"),
        ("audit_log", "user_id"),
        ("audit_log", "entity_id"),
    }
    for table_name, column_name in bigint_columns:
        assert isinstance(column(table_name, column_name).type, BigInteger), (
            table_name,
            column_name,
        )


def test_metadata_primary_keys_are_bigint_identity_by_default() -> None:
    """CA-15 en metadata: `BIGINT GENERATED BY DEFAULT AS IDENTITY`."""
    for table_name in EXPECTED_TABLES:
        table = Base.metadata.tables[table_name]
        primary_keys = list(table.primary_key.columns)
        assert [pk.name for pk in primary_keys] == ["id"], table_name

        identity = primary_keys[0].identity
        assert identity is not None, table_name
        assert identity.always is False, table_name
        assert primary_keys[0].nullable is False, table_name


def test_metadata_constraint_names_are_exact_and_not_double_prefixed() -> None:
    """Plan #3: nombres exactos de PK, FK, UQ y CHECK, sin `ck_ck_`."""
    for table in Base.metadata.tables.values():
        for constraint in table.constraints:
            assert constraint.name, (table.name, type(constraint).__name__)
            assert not str(constraint.name).startswith("ck_ck_"), constraint.name

    pks = {
        constraint.name
        for constraint in all_constraints()
        if isinstance(constraint, PrimaryKeyConstraint)
    }
    uqs = {
        constraint.name
        for constraint in all_constraints()
        if isinstance(constraint, UniqueConstraint)
    }
    cks = {
        constraint.name
        for constraint in all_constraints()
        if isinstance(constraint, CheckConstraint)
    }
    fks = {
        constraint.name
        for constraint in all_constraints()
        if isinstance(constraint, ForeignKeyConstraint)
    }

    assert pks == EXPECTED_PKS
    assert uqs == EXPECTED_UQS
    assert cks == EXPECTED_CKS
    assert fks == set(EXPECTED_FKS)
    assert len(pks) + len(uqs) + len(cks) + len(fks) == 43
    assert len(set().union(pks, uqs, cks, fks)) == 43


def test_metadata_indexes_have_exact_names_and_options() -> None:
    """Plan #4: indices no-PK, incluido el parcial y los trigram GIN."""
    indexes = {
        index.name: index for table in Base.metadata.tables.values() for index in table.indexes
    }
    unique_constraints = {
        constraint.name
        for constraint in all_constraints()
        if isinstance(constraint, UniqueConstraint)
    }

    assert set(indexes) | unique_constraints == EXPECTED_NON_PK_INDEXES
    assert len(indexes) == 16
    assert len(unique_constraints) == 11
    assert len(set(indexes) | unique_constraints) == 27
    assert "ix_patients_carnet" not in indexes

    carnet = indexes["uq_patients_carnet"]
    assert carnet.unique is True
    assert str(carnet.dialect_options["postgresql"]["where"]) == "carnet IS NOT NULL"

    for name in ("ix_doctors_name_trgm", "ix_patients_name_trgm"):
        index = indexes[name]
        assert index.dialect_options["postgresql"]["using"] == "gin"
        assert index.dialect_options["postgresql"]["ops"] == {"name": "gin_trgm_ops"}

    medications_trgm = indexes["ix_medications_description_trgm"]
    assert medications_trgm.dialect_options["postgresql"]["using"] == "gin"
    assert medications_trgm.dialect_options["postgresql"]["ops"] == {
        "(description::text)": "gin_trgm_ops"
    }

    for name in EXPECTED_VISIT_INDEXES:
        expressions = [str(expression) for expression in indexes[name].expressions]
        assert "visited_at DESC" in expressions, name
        assert "id DESC" in expressions, name


def test_metadata_fk_delete_rules_match_the_spec() -> None:
    """Plan #5: 10 RESTRICT, 1 CASCADE y 1 SET NULL."""
    fks = {
        constraint.name: constraint
        for constraint in all_constraints()
        if isinstance(constraint, ForeignKeyConstraint)
    }

    assert set(fks) == set(EXPECTED_FKS)
    for name, (table_name, target, ondelete) in EXPECTED_FKS.items():
        constraint = fks[name]
        assert constraint.table.name == table_name, name
        element = next(iter(constraint.elements))
        assert element.target_fullname == target, name
        assert element.ondelete == ondelete, name

    ondeletes = [next(iter(fk.elements)).ondelete for fk in fks.values()]
    assert ondeletes.count("RESTRICT") == 10
    assert ondeletes.count("CASCADE") == 1
    assert ondeletes.count("SET NULL") == 1


def test_relationships_match_the_spec_and_are_lazy_raise() -> None:
    """Todas las relaciones declaradas usan `lazy="raise"`."""
    found: set[tuple[str, str]] = set()
    for mapper in Base.registry.mappers:
        for relationship in mapper.relationships:
            key = (mapper.class_.__name__, relationship.key)
            found.add(key)
            assert relationship.lazy == "raise", key
            assert relationship.uselist is not EXPECTED_RELATIONSHIPS[key], key

    assert found == set(EXPECTED_RELATIONSHIPS)

    visit_medications = inspect(models.Visit).relationships["visit_medications"]
    assert visit_medications.cascade.delete_orphan is True
    assert visit_medications.cascade.delete is True
    assert visit_medications.cascade.save_update is True
    assert visit_medications.passive_deletes is True

    doctor_user = inspect(models.Doctor).relationships["user"]
    assert doctor_user.uselist is False
    assert doctor_user.back_populates == "doctor"

    created_by = inspect(models.Visit).relationships["created_by_user"]
    assert [local.name for local in created_by.local_columns] == ["created_by"]


def test_timestamp_defaults_and_orm_onupdate() -> None:
    """created_at/updated_at con default now() y onupdate solo en el ORM."""
    for table_name in EXPECTED_TABLES:
        created = column(table_name, "created_at")
        assert created.nullable is False, table_name
        assert created.server_default is not None, table_name
        assert str(created.server_default.arg) == "now()", table_name
        assert created.onupdate is None, table_name

    for table_name in TABLES_WITH_UPDATED_AT:
        updated = column(table_name, "updated_at")
        assert updated.nullable is False, table_name
        assert updated.server_default is not None, table_name
        assert str(updated.server_default.arg) == "now()", table_name
        assert updated.onupdate is not None, table_name

    assert "updated_at" not in Base.metadata.tables["roles"].c
    assert "updated_at" not in Base.metadata.tables["visit_medications"].c

    statement = update(models.Doctor).where(models.Doctor.id == 1).values(name="Nombre nuevo")
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "updated_at=now()" in compiled.replace(" ", "")


def test_boolean_columns_are_not_null_with_true_default() -> None:
    """Regla transversal: Boolean, `server_default=true`, `nullable=False`."""
    for table_name in (
        "doctors",
        "users",
        "drug_types",
        "brands",
        "locations",
        "medications",
        "patients",
    ):
        is_active = column(table_name, "is_active")
        assert is_active.nullable is False, table_name
        assert is_active.server_default is not None, table_name
        assert str(is_active.server_default.arg).lower() == "true", table_name


def test_status_and_visited_at_have_no_server_default() -> None:
    """`visits.status` y `visited_at` los envia siempre la capa de servicio."""
    assert column("visits", "status").server_default is None
    assert column("visits", "visited_at").server_default is None


def test_seed_sql_is_idempotent_by_construction() -> None:
    """Plan #8: el seed usa ON CONFLICT y no siembra usuarios ni contrasenas."""
    seed_module = load_migration(VERSIONS_DIR / "42de9c3ddd3a_seed_roles.py")

    seed_sql = seed_module.SEED_ROLES_SQL
    assert "ON CONFLICT (name) DO NOTHING" in seed_sql
    assert "password" not in seed_sql.lower()
    assert "INSERT INTO users" not in seed_sql
    assert "('ADMIN'" in seed_sql
    assert "('MEDICO'" in seed_sql

    unseed_sql = seed_module.UNSEED_ROLES_SQL
    assert "NOT EXISTS" in unseed_sql
    assert "users" in unseed_sql
    assert "DELETE FROM roles" in unseed_sql


def test_ca06_migration_set_is_a_single_linear_chain() -> None:
    """CA-6: 6 revisiones, una sola cabeza y el orden de la spec."""
    modules = migration_modules()
    assert len(modules) == 6

    bases = [module for module in modules.values() if module.down_revision is None]
    assert len(bases) == 1

    heads = [
        module
        for module in modules.values()
        if module.revision not in {m.down_revision for m in modules.values()}
    ]
    assert len(heads) == 1

    ordered: list[ModuleType] = []
    current = heads[0]
    while True:
        ordered.append(current)
        if current.down_revision is None:
            break
        assert isinstance(current.down_revision, str)
        current = modules[current.down_revision]

    messages = [(module.__doc__ or "").strip().splitlines()[0] for module in reversed(ordered)]
    assert messages == EXPECTED_REVISION_ORDER

    files = sorted(VERSIONS_DIR.glob("*.py"))
    assert len(files) == 6
    messages_by_file = []
    for path in files:
        source = path.read_text(encoding="utf-8")
        assert "def upgrade() -> None:" in source
        assert "def downgrade() -> None:" in source
        assert re.search(
            r"def downgrade\(\) -> None:\n(\s+\"\"\".*?\"\"\"\n)?\s+op\.",
            source,
            re.S,
        )

        module = load_migration(path)
        message = (module.__doc__ or "").strip().splitlines()[0]
        assert path.stem.endswith(message), path.name
        messages_by_file.append(message)

    assert set(messages_by_file) == set(EXPECTED_REVISION_ORDER)


def test_ca24_shared_infrastructure_files_are_not_modified() -> None:
    """CA-24 enmendado: infraestructura intacta salvo el flag de `session.py`.

    `feature-list.md` queda fuera del check: lo escribe el team-lead por diseno.
    """
    status = subprocess.run(
        [
            "git",
            "status",
            "--porcelain",
            "--",
            "docker-compose.yml",
            "alembic.ini",
            "alembic/env.py",
            "app/core/config.py",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert status.returncode == 0, status.stderr
    assert status.stdout.strip() == ""

    session_diff = subprocess.run(
        ["git", "diff", "--", "app/db/session.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert session_diff.returncode == 0, session_diff.stderr

    added_lines = [
        line[1:].strip()
        for line in session_diff.stdout.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    removed_lines = [
        line[1:].strip()
        for line in session_diff.stdout.splitlines()
        if line.startswith("-") and not line.startswith("---")
    ]

    assert removed_lines == []
    assert added_lines in ([], ["native_inet_types=False,"])

    session_source = (ROOT / "app" / "db" / "session.py").read_text(encoding="utf-8")
    assert session_source.count("native_inet_types=False") == 1
    assert "native_inet_types=False," in session_source


def test_ca24_feature_list_changes_are_owned_by_team_lead() -> None:
    """CA-24 enmendado: el cambio en `feature-list.md` no es un hallazgo."""
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", "feature-list.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert status.returncode == 0, status.stderr
    assert status.stdout.strip() in ("", "M feature-list.md", " M feature-list.md")


def test_alembic_env_uses_base_metadata() -> None:
    """`alembic/env.py` sigue usando la metadata de los modelos."""
    env_source = (ROOT / "alembic" / "env.py").read_text(encoding="utf-8")
    assert "target_metadata = Base.metadata" in env_source
    assert "from app import models" in env_source


@pytest.mark.parametrize("table_name", sorted(EXPECTED_TABLES))
def test_each_table_has_created_at(table_name: str) -> None:
    """Todas las tablas del dominio registran `created_at` por la base."""
    created_at = column(table_name, "created_at")
    assert isinstance(created_at.type, DateTime)
    assert created_at.type.timezone is True
    assert created_at.nullable is False
    assert created_at.server_default is not None
    assert str(created_at.server_default.arg) == "now()"


def test_uq_patients_carnet_is_an_index_not_a_column_unique() -> None:
    """La unicidad del carnet es parcial (NULL multiples) y no `unique=True`."""
    carnet = column("patients", "carnet")
    assert carnet.unique is not True
    assert carnet.index is not True
    assert "uq_patients_carnet" in {
        index.name for index in Base.metadata.tables["patients"].indexes
    }
