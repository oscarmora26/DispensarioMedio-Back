"""Tests de integracion de las migraciones Alembic (CA-5, CA-17 a CA-21).

Ejecutan la CLI real de Alembic sobre la base resuelta por
`resolve_test_database_url` (`TEST_DATABASE_URL` o BD de desarrollo).
Cada test restaura `head` al terminar para no dejar la base a medias.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.db_support import (
    fetch_scalar_in_new_connection,
    require_migrated_database,
    resolve_test_database_url,
)

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_SECONDS = 180
USERS_BACKUP_SQL = """
    SELECT u.id, u.username, u.email, u.password_hash, r.name AS role,
           u.doctor_id, u.is_active, u.last_login_at
    FROM users u
    JOIN roles r ON r.id = u.role_id
    ORDER BY u.id
"""
DOCTORS_BACKUP_SQL = """
    SELECT id, name, cedula, lab_shift, specialty, is_active, created_at, updated_at
    FROM doctors
    ORDER BY id
"""


async def _fetch_rows(sql: str) -> list[dict[str, Any]]:
    """Lee filas en una conexion nueva fuera de la transaccion de los tests."""
    engine = create_async_engine(resolve_test_database_url(), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(sql))
            return [dict(row) for row in result.mappings().all()]
    finally:
        await engine.dispose()


async def _execute(sql: str, params: dict[str, Any] | None = None) -> None:
    """Ejecuta una sentencia en una conexion nueva (autocommit)."""
    engine = create_async_engine(resolve_test_database_url(), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(sql), params or {})
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
async def preserve_permanent_dev_accounts() -> AsyncIterator[None]:
    """Protege los usuarios y medicos ya existentes ante `downgrade base`.

    La BD bajo prueba puede contener cuentas permanentes creadas por el humano
    (`admin` y `medico` en desarrollo). Los tests de migraciones legitiman
    `downgrade base`, que borra todas las tablas; este fixture guarda esas
    filas y las restaura despues de cada test. Las filas que el propio test
    crea no se tocan.

    Falla con el mismo mensaje accionable que `db_engine` si la base no esta
    migrada (estos tests no piden `db_engine`, asi que verifican por su cuenta
    antes de tocar tablas de dominio).
    """
    check_engine = create_async_engine(resolve_test_database_url(), poolclass=NullPool)
    try:
        async with check_engine.connect() as check_conn:
            await require_migrated_database(check_conn)
    finally:
        await check_engine.dispose()
    doctors = await _fetch_rows(DOCTORS_BACKUP_SQL)
    users = await _fetch_rows(USERS_BACKUP_SQL)
    yield
    for doctor in doctors:
        await _execute(
            """
            INSERT INTO doctors (
                id, name, cedula, lab_shift, specialty, is_active, created_at, updated_at
            )
            VALUES (
                :id, :name, :cedula, :lab_shift, :specialty, :is_active, :created_at, :updated_at
            )
            ON CONFLICT (id) DO NOTHING
            """,
            doctor,
        )
    for user in users:
        await _execute(
            """
            INSERT INTO users (
                id, username, email, password_hash, role_id, doctor_id, is_active, last_login_at
            )
            VALUES (
                :id, :username, :email, :password_hash,
                (SELECT id FROM roles WHERE name = :role),
                :doctor_id, :is_active, :last_login_at
            )
            ON CONFLICT (username) DO NOTHING
            """,
            user,
        )
    if users:
        await _execute(
            "SELECT setval(pg_get_serial_sequence('users', 'id'), (SELECT max(id) FROM users))"
        )
    if doctors:
        await _execute(
            "SELECT setval(pg_get_serial_sequence('doctors', 'id'), (SELECT max(id) FROM doctors))"
        )


def run_alembic(*args: str) -> subprocess.CompletedProcess[str]:
    """Ejecuta `python -m alembic` contra la BD de la suite desde la raiz."""
    env = {**os.environ, "DATABASE_URL": resolve_test_database_url()}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
        env=env,
    )


def alembic_ok(*args: str) -> subprocess.CompletedProcess[str]:
    """Ejecuta Alembic y falla mostrando stdout/stderr si el codigo no es 0."""
    result = run_alembic(*args)
    assert result.returncode == 0, (
        f"`alembic {' '.join(args)}` fallo con codigo {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    return result


async def scalar(sql: str) -> object:
    """Consulta escalar en una conexion nueva (sin transaccion de test abierta)."""
    return await fetch_scalar_in_new_connection(sql)


async def test_ca18_alembic_check_reports_no_new_operations() -> None:
    """CA-18: modelos y base alineados; `alembic check` limpio."""
    result = alembic_ok("check")

    assert "No new upgrade operations detected." in (result.stdout + result.stderr)


async def test_ca05_alembic_reports_a_single_head() -> None:
    """CA-5/CA-6: `alembic heads` muestra una unica cabeza."""
    result = alembic_ok("heads")
    lines = [line for line in result.stdout.splitlines() if line.strip()]

    assert len(lines) == 1
    assert "(head)" in lines[0]

    current = alembic_ok("current")
    assert lines[0].split()[0] in current.stdout


async def test_ca21_upgrade_head_is_idempotent() -> None:
    """CA-21: ejecutar `upgrade head` dos veces no falla ni duplica la version."""
    alembic_ok("upgrade", "head")
    alembic_ok("upgrade", "head")

    assert await scalar("SELECT count(*) FROM alembic_version") == 1


async def test_ca17_downgrade_one_and_upgrade_restores_seed() -> None:
    """CA-17: bajar el seed respeta los roles en uso y volver a subir deja los dos roles.

    La BD de desarrollo tiene cuentas permanentes (`admin`/`medico`) que referencian
    ambos roles, por lo que `UNSEED_ROLES_SQL` no los borra (R-6). El test comprueba
    que los roles referenciados sobreviven y que `upgrade head` restaura el seed.
    """
    roles_before = {
        row["name"] for row in await _fetch_rows("SELECT name FROM roles ORDER BY name")
    }
    roles_in_use = {
        row["name"]
        for row in await _fetch_rows(
            """
            SELECT DISTINCT r.name FROM roles r
            JOIN users u ON u.role_id = r.id
            """
        )
    }
    try:
        alembic_ok("downgrade", "-1")

        roles_after = {
            row["name"] for row in await _fetch_rows("SELECT name FROM roles ORDER BY name")
        }
        assert roles_in_use <= roles_after
        assert roles_after <= roles_before

        alembic_ok("upgrade", "head")
        roles = await scalar("SELECT count(*) FROM roles")
        assert roles == 2
    finally:
        alembic_ok("upgrade", "head")


async def test_ca19_downgrade_base_leaves_only_alembic_version() -> None:
    """CA-19: sin tablas de dominio, sin indices ni extensiones."""
    try:
        alembic_ok("downgrade", "base")

        tables = await scalar(
            """
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """
        )
        indexes = await scalar(
            """
            SELECT count(*) FROM pg_indexes
            WHERE schemaname = 'public' AND indexname NOT LIKE 'alembic_version%'
            """
        )
        extensions = await scalar(
            "SELECT count(*) FROM pg_extension WHERE extname IN ('citext', 'pg_trgm')"
        )
        version_rows = await scalar("SELECT count(*) FROM alembic_version")

        assert tables == 1
        assert indexes == 0
        assert extensions == 0
        assert version_rows == 0
    finally:
        alembic_ok("upgrade", "head")


async def test_ca20_full_roundtrip_restores_the_expected_schema() -> None:
    """CA-20: downgrade/upgrade dos veces y CA-7/CA-16 vigentes al final."""
    try:
        for _ in range(2):
            alembic_ok("downgrade", "base")
            alembic_ok("upgrade", "head")

        tables = await scalar(
            """
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """
        )
        indexes = await scalar(
            """
            SELECT count(*) FROM pg_indexes
            WHERE schemaname = 'public' AND indexname NOT LIKE 'alembic_version%'
            """
        )
        extensions = await scalar(
            """
            SELECT count(*) FROM pg_extension
            WHERE extname IN ('citext', 'pg_trgm')
            """
        )
        roles = await scalar("SELECT count(*) FROM roles")
        users = await scalar("SELECT count(*) FROM users")

        assert tables == 12
        assert indexes == 38
        assert extensions == 2
        assert roles == 2
        assert users == 0
    finally:
        alembic_ok("upgrade", "head")


async def test_ca20_after_roundtrip_alembic_check_is_still_clean() -> None:
    """CA-20/CA-18: tras el round-trip no aparecen diferencias espurias."""
    result = alembic_ok("check")

    assert "No new upgrade operations detected." in (result.stdout + result.stderr)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (("current",), "head"),
        (("history",), "seed_roles"),
    ],
)
async def test_alembic_cli_basics_work(command: tuple[str, ...], expected: str) -> None:
    """Las ordenes de diagnostico usadas en los criterios siguen funcionando."""
    result = alembic_ok(*command)

    assert expected in result.stdout
