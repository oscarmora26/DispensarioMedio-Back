"""Tests de integracion del catalogo PostgreSQL (CA-7 a CA-16).

Requieren la base migrada con `uv run alembic upgrade head` y son de solo
lectura: consultan `information_schema` y `pg_catalog` dentro de una
transaccion que se revierte.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection

from tests.db_support import fetch_all, fetch_scalar
from tests.test_models_metadata import (
    EXPECTED_CKS,
    EXPECTED_FK_INDEXES,
    EXPECTED_FKS,
    EXPECTED_PKS,
    EXPECTED_TABLES,
    EXPECTED_TRGM_INDEXES,
    EXPECTED_UQS,
    EXPECTED_VISIT_INDEXES,
)

EXPECTED_UNIQUE_INDEXES = EXPECTED_UQS | {"uq_patients_carnet"}


async def test_ca07_twelve_physical_tables_in_public_schema(db_conn: AsyncConnection) -> None:
    """CA-7: 11 tablas de dominio + `alembic_version`."""
    count = await fetch_scalar(
        db_conn,
        """
        SELECT count(*) FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        """,
    )

    assert count == 12


async def test_ca08_only_citext_and_pg_trgm_extensions(db_conn: AsyncConnection) -> None:
    """CA-8: extensiones exactas, sin `pg_stat_statements` ni `hypopg`."""
    extensions = await fetch_all(
        db_conn,
        """
        SELECT extname FROM pg_extension
        WHERE extname IN ('citext', 'pg_trgm')
        ORDER BY 1
        """,
    )
    forbidden = await fetch_scalar(
        db_conn,
        """
        SELECT count(*) FROM pg_extension
        WHERE extname IN ('pg_stat_statements', 'hypopg')
        """,
    )

    assert [row[0] for row in extensions] == ["citext", "pg_trgm"]
    assert forbidden == 0


async def test_ca09_constraint_counts_by_type(db_conn: AsyncConnection) -> None:
    """CA-9: 9 CHECK, 12 FK, 11 PK y 11 UNIQUE (43 en total)."""
    rows = await fetch_all(
        db_conn,
        """
        SELECT contype::text, count(*)::int FROM pg_constraint
        WHERE connamespace = 'public'::regnamespace
          AND conname NOT LIKE 'alembic_version%'
        GROUP BY contype
        ORDER BY contype::text
        """,
    )

    assert {row[0]: row[1] for row in rows} == {"c": 9, "f": 12, "p": 11, "u": 11}
    assert sum(row[1] for row in rows) == 43


async def test_ca10_foreign_keys_names_references_and_delete_actions(
    db_conn: AsyncConnection,
) -> None:
    """CA-10: 12 FK con nombre exacto; `r` RESTRICT, `c` CASCADE, `n` SET NULL."""
    rows = await fetch_all(
        db_conn,
        """
        SELECT conname, confdeltype::text, confrelid::regclass::text
        FROM pg_constraint
        WHERE contype = 'f' AND connamespace = 'public'::regnamespace
        ORDER BY conname
        """,
    )

    assert len(rows) == 12
    found = {name: (delete_action, target) for name, delete_action, target in rows}
    expected = {
        name: ({"RESTRICT": "r", "CASCADE": "c", "SET NULL": "n"}[ondelete], target.split(".")[0])
        for name, (_, target, ondelete) in EXPECTED_FKS.items()
    }
    assert found == expected


async def test_ca11_check_constraint_names(db_conn: AsyncConnection) -> None:
    """CA-11: las 9 CHECK con el nombre exacto de la spec."""
    rows = await fetch_all(
        db_conn,
        """
        SELECT conname FROM pg_constraint
        WHERE contype = 'c' AND connamespace = 'public'::regnamespace
        ORDER BY conname
        """,
    )

    assert {row[0] for row in rows} == EXPECTED_CKS
    assert len(rows) == 9


async def test_ca12_index_count_and_groups(db_conn: AsyncConnection) -> None:
    """CA-12: 38 indices repartidos en PK, UQ, FK, visitas y trigram."""
    rows = await fetch_all(
        db_conn,
        """
        SELECT indexname FROM pg_indexes
        WHERE schemaname = 'public' AND indexname NOT LIKE 'alembic_version%'
        ORDER BY indexname
        """,
    )
    names = {row[0] for row in rows}

    assert len(names) == 38
    assert {name for name in names if name.startswith("pk_")} == EXPECTED_PKS
    assert (names & EXPECTED_UNIQUE_INDEXES) == EXPECTED_UNIQUE_INDEXES
    assert len(names & EXPECTED_UNIQUE_INDEXES) == 12
    assert (names & EXPECTED_FK_INDEXES) == EXPECTED_FK_INDEXES
    assert (names & EXPECTED_VISIT_INDEXES) == EXPECTED_VISIT_INDEXES
    assert (names & EXPECTED_TRGM_INDEXES) == EXPECTED_TRGM_INDEXES

    grouped = (
        EXPECTED_PKS
        | EXPECTED_UNIQUE_INDEXES
        | EXPECTED_FK_INDEXES
        | EXPECTED_VISIT_INDEXES
        | EXPECTED_TRGM_INDEXES
    )
    assert names == grouped


async def test_ca12_visit_indexes_are_descending(db_conn: AsyncConnection) -> None:
    """CA-12: los cuatro indices de visitas terminan en `visited_at DESC, id DESC`."""
    rows = await fetch_all(
        db_conn,
        """
        SELECT indexname, indexdef FROM pg_indexes
        WHERE schemaname = 'public' AND indexname = ANY(:names)
        ORDER BY indexname
        """,
        {"names": sorted(EXPECTED_VISIT_INDEXES)},
    )

    assert {row[0] for row in rows} == EXPECTED_VISIT_INDEXES
    for name, indexdef in rows:
        assert "visited_at DESC, id DESC" in indexdef, name


async def test_ca12_trigram_indexes_use_gin(db_conn: AsyncConnection) -> None:
    """CA-12: los tres indices trigram usan `USING gin` y `gin_trgm_ops`."""
    rows = await fetch_all(
        db_conn,
        """
        SELECT indexname, indexdef FROM pg_indexes
        WHERE schemaname = 'public' AND indexname = ANY(:names)
        ORDER BY indexname
        """,
        {"names": sorted(EXPECTED_TRGM_INDEXES)},
    )

    assert {row[0] for row in rows} == EXPECTED_TRGM_INDEXES
    for name, indexdef in rows:
        assert "USING gin" in indexdef, name
        assert "gin_trgm_ops" in indexdef, name

    medication_index = next(
        indexdef for name, indexdef in rows if name.endswith("_trgm") and "medications" in indexdef
    )
    assert "description)::text" in medication_index or "description::text" in medication_index


async def test_ca12_audit_log_only_has_primary_key_index(db_conn: AsyncConnection) -> None:
    """R-2 aprobado: `audit_log` no lleva indice sobre `user_id` en v1."""
    rows = await fetch_all(
        db_conn,
        """
        SELECT indexname FROM pg_indexes
        WHERE schemaname = 'public' AND tablename = 'audit_log'
        ORDER BY indexname
        """,
    )

    assert [row[0] for row in rows] == ["pk_audit_log"]


async def test_ca13_carnet_unique_index_is_partial(db_conn: AsyncConnection) -> None:
    """CA-13: `uq_patients_carnet` unico y parcial; no existe `ix_patients_carnet`."""
    indexdef = await fetch_scalar(
        db_conn,
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_patients_carnet'",
    )
    wrong_index = await fetch_scalar(
        db_conn,
        "SELECT count(*) FROM pg_indexes WHERE indexname = 'ix_patients_carnet'",
    )

    assert "CREATE UNIQUE INDEX" in indexdef
    assert "WHERE (carnet IS NOT NULL)" in indexdef
    assert wrong_index == 0


async def test_ca14_critical_column_types_and_defaults(db_conn: AsyncConnection) -> None:
    """CA-14: tipos, longitudes, precision y defaults de columnas criticas."""
    rows = await fetch_all(
        db_conn,
        """
        SELECT table_name, column_name, udt_name, data_type, is_nullable,
               column_default, numeric_precision, numeric_scale,
               character_maximum_length
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND (table_name, column_name) IN (
            ('roles', 'created_at'), ('users', 'username'), ('users', 'email'),
            ('users', 'password_hash'), ('patients', 'carnet'),
            ('patients', 'patient_type'), ('visits', 'visited_at'),
            ('visits', 'status'), ('visits', 'annulled_at'),
            ('visit_medications', 'quantity'), ('audit_log', 'old_data'),
            ('audit_log', 'new_data'), ('audit_log', 'ip'), ('audit_log', 'action'),
            ('doctors', 'cedula'), ('medications', 'description'),
            ('locations', 'shelf')
          )
        ORDER BY table_name, column_name
        """,
    )
    columns = {
        (table, name): {
            "udt_name": udt_name,
            "data_type": data_type,
            "is_nullable": is_nullable,
            "default": default,
            "numeric_precision": precision,
            "numeric_scale": scale,
            "length": length,
        }
        for table, name, udt_name, data_type, is_nullable, default, precision, scale, length in rows
    }

    assert len(columns) == 17

    quantity = columns[("visit_medications", "quantity")]
    assert quantity["udt_name"] == "numeric"
    assert (quantity["numeric_precision"], quantity["numeric_scale"]) == (10, 2)
    assert quantity["is_nullable"] == "YES"

    assert columns[("audit_log", "old_data")]["udt_name"] == "jsonb"
    assert columns[("audit_log", "new_data")]["udt_name"] == "jsonb"
    assert columns[("audit_log", "ip")]["udt_name"] == "inet"

    for key in (
        ("users", "username"),
        ("users", "email"),
        ("patients", "carnet"),
        ("medications", "description"),
        ("locations", "shelf"),
    ):
        assert columns[key]["udt_name"] == "citext", key
        assert columns[key]["data_type"] == "USER-DEFINED", key

    assert columns[("doctors", "cedula")]["udt_name"] == "varchar"
    assert columns[("doctors", "cedula")]["length"] == 11
    assert columns[("users", "password_hash")]["length"] == 255
    assert columns[("visits", "status")]["udt_name"] == "varchar"
    assert columns[("visits", "status")]["length"] == 20
    assert columns[("patients", "patient_type")]["length"] == 20
    assert columns[("audit_log", "action")]["length"] == 30

    for key in (("roles", "created_at"), ("visits", "visited_at"), ("visits", "annulled_at")):
        assert columns[key]["udt_name"] == "timestamptz", key

    assert columns[("roles", "created_at")]["default"] == "now()"
    assert columns[("visits", "visited_at")]["default"] is None
    assert columns[("visits", "annulled_at")]["default"] is None


async def test_ca14_timestamp_columns_default_now_only_on_creation(
    db_conn: AsyncConnection,
) -> None:
    """CA-14/regla 17: `now()` en columnas de creacion, no en fechas de negocio."""
    rows = await fetch_all(
        db_conn,
        """
        SELECT table_name, column_name, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND column_name LIKE '%_at'
        ORDER BY table_name, column_name
        """,
    )

    for table_name, column_name, default in rows:
        if column_name in {"created_at", "updated_at"}:
            assert default == "now()", (table_name, column_name)
        else:
            assert default is None, (table_name, column_name)


async def test_ca14_boolean_columns_default_true(db_conn: AsyncConnection) -> None:
    """Regla transversal: `is_active` es NOT NULL con default `true`."""
    rows = await fetch_all(
        db_conn,
        """
        SELECT table_name, column_default, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public' AND column_name = 'is_active'
        ORDER BY table_name
        """,
    )

    assert len(rows) == 7
    for table_name, default, is_nullable in rows:
        assert default == "true", table_name
        assert is_nullable == "NO", table_name


async def test_ca15_primary_keys_are_identity_by_default(db_conn: AsyncConnection) -> None:
    """CA-15: las 11 PK son `BIGINT GENERATED BY DEFAULT AS IDENTITY`."""
    rows = await fetch_all(
        db_conn,
        """
        SELECT table_name, is_identity, identity_generation
        FROM information_schema.columns
        WHERE table_schema = 'public' AND column_name = 'id' AND is_identity = 'YES'
        ORDER BY table_name
        """,
    )

    assert len(rows) == 11
    assert {table_name for table_name, _, _ in rows} == EXPECTED_TABLES
    for table_name, is_identity, generation in rows:
        assert is_identity == "YES", table_name
        assert generation == "BY DEFAULT", table_name


async def test_ca16_seed_roles_without_users_or_plaintext_passwords(
    db_conn: AsyncConnection,
) -> None:
    """CA-16: exactamente ADMIN y MEDICO, 0 usuarios, 0 columnas de contrasena en claro."""
    roles = await fetch_all(db_conn, "SELECT name FROM roles ORDER BY name")
    users = await fetch_scalar(db_conn, "SELECT count(*) FROM users")
    password_columns = await fetch_all(
        db_conn,
        """
        SELECT table_schema, table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND column_name ILIKE '%password%'
          AND column_name <> 'password_hash'
        """,
    )

    assert [row[0] for row in roles] == ["ADMIN", "MEDICO"]
    assert users == 0
    assert password_columns == []


async def test_alembic_version_has_exactly_one_head_row(db_conn: AsyncConnection) -> None:
    """CA-5: una unica cabeza aplicada en `alembic_version`."""
    count = await fetch_scalar(db_conn, "SELECT count(*) FROM alembic_version")

    assert count == 1


async def test_roles_and_visit_medications_have_no_updated_at(db_conn: AsyncConnection) -> None:
    """§6.1 y §6.10: estas dos tablas no declaran `updated_at`."""
    rows = await fetch_all(
        db_conn,
        """
        SELECT table_name FROM information_schema.columns
        WHERE table_schema = 'public' AND column_name = 'updated_at'
        ORDER BY table_name
        """,
    )

    tables = {row[0] for row in rows}
    assert "roles" not in tables
    assert "visit_medications" not in tables
    assert len(tables) == 8


async def test_no_triggers_are_created_in_v1(db_conn: AsyncConnection) -> None:
    """Decision transversal: `updated_at` no se refresca con triggers."""
    count = await fetch_scalar(
        db_conn,
        """
        SELECT count(*) FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND NOT t.tgisinternal
        """,
    )

    assert count == 0
