"""Tests del CLI `create-admin` (FEAT-002, CA-34 a CA-38)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.cli import main as cli_main
from tests.auth_support import (
    LOGIN_PATH,
    count_users_by_username,
    create_auth_user,
    fetch_user,
)
from tests.db_support import unique_suffix

ROOT = Path(__file__).resolve().parent.parent
ADMIN_PASSWORD = "CliAdminPass123"


def cli_session_factory(conn: AsyncConnection) -> object:
    """Devuelve un `SessionLocal` de prueba unido a la transaccion del test."""

    def factory() -> AsyncSession:
        return AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

    return factory


async def test_ca34_help_and_invalid_arguments(
    db_conn: AsyncConnection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CA-34: `--help` sale con 0 y una entrada invalida sale con 2 sin tocar la base."""
    help_result = subprocess.run(
        [sys.executable, "-m", "app.cli", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert help_result.returncode == 0
    assert "create-admin" in help_result.stdout
    assert "--password" not in help_result.stdout + help_result.stderr
    assert "--admin-password" not in help_result.stdout + help_result.stderr

    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setattr("app.cli.SessionLocal", cli_session_factory(db_conn))

    code = await cli_main(["create-admin", "--username", "ab", "--email", "a@b.co"])

    assert code == 2
    assert await count_users_by_username(db_conn, "ab") == 0
    assert "Entrada inválida" in capsys.readouterr().err


async def test_ca35_creates_admin_and_allows_login(
    auth_client: AsyncClient,
    db_conn: AsyncConnection,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CA-35: crea el ADMIN con hash real y el login HTTP funciona."""
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setattr("app.cli.SessionLocal", cli_session_factory(db_conn))
    username = f"cli_admin_{unique_suffix()}"

    code = await cli_main(
        ["create-admin", "--username", username, "--email", f"{username}@example.test"]
    )

    assert code == 0

    result = await db_conn.execute(
        text(
            """
            SELECT u.id, u.username, u.password_hash, u.is_active, u.doctor_id,
                   u.last_login_at, r.name AS role
            FROM users u JOIN roles r ON r.id = u.role_id
            WHERE u.username = :username
            """
        ),
        {"username": username},
    )
    row = result.mappings().one()
    assert row["role"] == "ADMIN"
    assert row["is_active"] is True
    assert row["doctor_id"] is None
    assert row["last_login_at"] is None
    assert row["password_hash"].startswith("$argon2id$")

    login = await auth_client.post(
        LOGIN_PATH, json={"username": username, "password": ADMIN_PASSWORD}
    )
    assert login.status_code == 200

    captured = capsys.readouterr()
    assert username in captured.out
    assert ADMIN_PASSWORD not in captured.out + captured.err
    assert row["password_hash"] not in captured.out + captured.err


async def test_ca36_duplicates_do_not_overwrite(
    auth_client: AsyncClient, db_conn: AsyncConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CA-36: duplicado por username o email => 1 sin sobrescribir el hash."""
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setattr("app.cli.SessionLocal", cli_session_factory(db_conn))
    username = f"cli_admin_{unique_suffix()}"
    email = f"{username}@example.test"

    assert await cli_main(["create-admin", "--username", username, "--email", email]) == 0
    user_id = (
        await db_conn.execute(
            text("SELECT id FROM users WHERE username = :username"), {"username": username}
        )
    ).scalar_one()
    original_hash = (await fetch_user(db_conn, int(user_id)))["password_hash"]

    same_username = await cli_main(
        [
            "create-admin",
            "--username",
            username.upper(),
            "--email",
            f"otro_{unique_suffix()}@x.test",
        ]
    )
    assert same_username == 1
    assert await count_users_by_username(db_conn, username) == 1
    assert (await fetch_user(db_conn, int(user_id)))["password_hash"] == original_hash

    same_email = await cli_main(
        [
            "create-admin",
            "--username",
            f"otro_{unique_suffix()}",
            "--email",
            email.upper(),
        ]
    )
    assert same_email == 1
    assert (await fetch_user(db_conn, int(user_id)))["password_hash"] == original_hash

    login = await auth_client.post(
        LOGIN_PATH, json={"username": username, "password": ADMIN_PASSWORD}
    )
    assert login.status_code == 200


@pytest.mark.parametrize(
    ("case", "username", "email", "password", "gets"),
    [
        ("short_username", "abc", "a@b.co", ADMIN_PASSWORD, None),
        ("username_with_space", "cli admin", "a@b.co", ADMIN_PASSWORD, None),
        ("email_without_at", "cli_admin", "sinarroba", ADMIN_PASSWORD, None),
        ("email_without_domain", "cli_admin", "user@dominio", ADMIN_PASSWORD, None),
        ("short_password", "cli_admin", "a@b.co", "1234567", None),
        ("eof", "cli_admin", "a@b.co", None, "eof"),
        ("mismatch", "cli_admin", "a@b.co", None, "mismatch"),
    ],
)
async def test_ca37_invalid_inputs_exit_2_without_writing(
    db_conn: AsyncConnection,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    username: str,
    email: str,
    password: str | None,
    gets: str | None,
) -> None:
    """CA-37: entradas invalidas => 2 y ninguna fila creada."""
    monkeypatch.setattr("app.cli.SessionLocal", cli_session_factory(db_conn))
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    if password is not None:
        monkeypatch.setenv("ADMIN_PASSWORD", password)

    calls: list[str] = []

    if gets == "eof":

        def raise_eof(_prompt: str) -> str:
            raise EOFError

        monkeypatch.setattr("app.cli.getpass", raise_eof)
    elif gets == "mismatch":
        answers = iter(["PrimeraClave123", "SegundaClave123"])

        def two_answers(_prompt: str) -> str:
            calls.append(_prompt)
            return next(answers)

        monkeypatch.setattr("app.cli.getpass", two_answers)

    code = await cli_main(["create-admin", "--username", username, "--email", email])

    assert code == 2, case
    assert await count_users_by_username(db_conn, username) == 0, case
    if gets == "mismatch":
        assert len(calls) == 2


async def test_ca38_missing_admin_role_and_database_error(
    db_conn: AsyncConnection,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CA-38: rol ADMIN ausente => 3; `SQLAlchemyError` => 4 por stderr."""
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setattr("app.cli.SessionLocal", cli_session_factory(db_conn))

    await db_conn.execute(
        text(
            "UPDATE users SET role_id = (SELECT id FROM roles WHERE name = 'MEDICO') "
            "WHERE role_id = (SELECT id FROM roles WHERE name = 'ADMIN')"
        )
    )
    await db_conn.execute(text("DELETE FROM roles WHERE name = 'ADMIN'"))

    missing_role = await cli_main(
        ["create-admin", "--username", "cli_admin", "--email", "cli_admin@example.test"]
    )

    assert missing_role == 3
    assert "alembic upgrade head" in capsys.readouterr().err

    async def failing_create_admin(*_args: object, **_kwargs: object) -> None:
        raise SQLAlchemyError("boom")

    monkeypatch.setattr("app.cli.create_admin", failing_create_admin)
    database_error = await cli_main(
        ["create-admin", "--username", "cli_admin2", "--email", "cli_admin2@example.test"]
    )

    assert database_error == 4
    assert "Error de base de datos" in capsys.readouterr().err


async def test_duplicate_username_colliding_with_other_user_email(
    db_conn: AsyncConnection,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regresion MAJOR ciclo 1: username de A y email de B en la misma peticion.

    El contrato (CA-36) exige codigo 1 (`El usuario ya existe`) al detectar un
    duplicado por cualquiera de los dos campos, sin crear una fila nueva.
    """
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setattr("app.cli.SessionLocal", cli_session_factory(db_conn))

    first = f"dup_a_{unique_suffix()}"
    second = f"dup_b_{unique_suffix()}"
    await create_auth_user(db_conn, username=first, email=f"{first}@example.test")
    await create_auth_user(db_conn, username=second, email=f"{second}@example.test")

    code = await cli_main(
        ["create-admin", "--username", first.upper(), "--email", f"{second.upper()}@example.test"]
    )

    assert code == 1
    assert "El usuario ya existe" in capsys.readouterr().err
    assert await count_users_by_username(db_conn, first) == 1
    assert await count_users_by_username(db_conn, second) == 1
    assert await count_users_by_username(db_conn, first.upper()) == 1
