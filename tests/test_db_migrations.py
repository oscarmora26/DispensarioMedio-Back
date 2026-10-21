"""Tests de integracion de las migraciones Alembic (CA-5, CA-17 a CA-21).

Ejecutan la CLI real de Alembic sobre la base configurada en `DATABASE_URL`.
Cada test restaura `head` al terminar para no dejar la base a medias.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.db_support import fetch_scalar_in_new_connection

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_SECONDS = 180


def run_alembic(*args: str) -> subprocess.CompletedProcess[str]:
    """Ejecuta `python -m alembic` desde la raiz del repositorio."""
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
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
    """CA-17: bajar el seed y volver a subir deja exactamente dos roles."""
    try:
        alembic_ok("downgrade", "-1")
        assert await scalar("SELECT count(*) FROM roles") == 0

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
