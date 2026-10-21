"""CLI de administracion del dispensario medico."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from collections.abc import Sequence
from getpass import getpass

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.exceptions import ConflictError
from app.db.session import SessionLocal, engine
from app.services.user_service import AdminRoleMissingError, create_admin

USERNAME_PATTERN = re.compile(r"\S{4,50}")
EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
MAX_EMAIL_LENGTH = 255

DUPLICATE_MESSAGE = "El usuario ya existe"
DATABASE_ERROR_MESSAGE = "Error de base de datos al crear el usuario"
INVALID_INPUT_MESSAGE = "Entrada inválida"


def build_parser() -> argparse.ArgumentParser:
    """Construye el parser de argumentos del CLI."""
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Tareas administrativas del dispensario medico.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_admin_parser = subparsers.add_parser(
        "create-admin",
        help="Crea el administrador inicial.",
        description=(
            "Crea el usuario administrador inicial. La contrasena se toma de "
            "ADMIN_PASSWORD o se pide interactivamente; nunca por argumento."
        ),
    )
    create_admin_parser.add_argument("--username", required=True, help="Nombre de usuario")
    create_admin_parser.add_argument("--email", required=True, help="Correo electronico")
    return parser


def _error(message: str) -> int:
    """Escribe un mensaje en stderr y devuelve el codigo de entrada invalida."""
    print(message, file=sys.stderr)
    return 2


def _read_password() -> str | None:
    """Lee la contrasena de `ADMIN_PASSWORD` o la pide dos veces por consola."""
    env_password = os.environ.get("ADMIN_PASSWORD", "")
    if env_password:
        return env_password

    try:
        password = getpass("Contraseña: ")
        confirmation = getpass("Confirmar contraseña: ")
    except EOFError:
        return None

    if password != confirmation:
        return None
    return password


def _is_unique_violation(exc: IntegrityError) -> bool:
    """Distingue un duplicado de otro error de integridad."""
    original = exc.orig
    if getattr(original, "sqlstate", None) == "23505":
        return True
    if getattr(original, "pgcode", None) == "23505":
        return True
    return "23505" in str(original).lower() or "duplicate key" in str(original).lower()


async def _create_admin(username: str, email: str) -> int:
    """Valida la entrada, crea el administrador y traduce errores a codigos de salida."""
    stripped_username = username.strip()
    stripped_email = email.strip()

    if not USERNAME_PATTERN.fullmatch(stripped_username):
        return _error(
            f"{INVALID_INPUT_MESSAGE}: el username debe tener 4..50 caracteres y sin espacios"
        )
    if len(stripped_email) > MAX_EMAIL_LENGTH or not EMAIL_PATTERN.fullmatch(stripped_email):
        return _error(f"{INVALID_INPUT_MESSAGE}: el email no tiene un formato valido")

    password = _read_password()
    if password is None:
        return _error(f"{INVALID_INPUT_MESSAGE}: no se pudo leer la contraseña")
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        return _error(
            f"{INVALID_INPUT_MESSAGE}: la contraseña debe tener "
            f"{MIN_PASSWORD_LENGTH}..{MAX_PASSWORD_LENGTH} caracteres"
        )

    try:
        async with SessionLocal() as session:
            await create_admin(
                session,
                username=stripped_username,
                email=stripped_email,
                password=password,
            )
            await session.commit()
    except AdminRoleMissingError:
        print(
            "No existe el rol ADMIN. Ejecute 'alembic upgrade head' "
            "antes de crear el administrador.",
            file=sys.stderr,
        )
        return 3
    except ConflictError:
        print(DUPLICATE_MESSAGE, file=sys.stderr)
        return 1
    except IntegrityError as exc:
        if _is_unique_violation(exc):
            print(DUPLICATE_MESSAGE, file=sys.stderr)
            return 1
        print(DATABASE_ERROR_MESSAGE, file=sys.stderr)
        return 4
    except SQLAlchemyError:
        print(DATABASE_ERROR_MESSAGE, file=sys.stderr)
        return 4
    finally:
        await engine.dispose()

    print(f"Usuario '{stripped_username}' creado con rol ADMIN.")
    return 0


async def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada asincrono; devuelve el codigo de salida del proceso."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2

    if args.command == "create-admin":
        return await _create_admin(args.username, args.email)
    return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
