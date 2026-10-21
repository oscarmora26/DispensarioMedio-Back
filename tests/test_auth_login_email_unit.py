"""Tests unitarios de FEAT-005: `LoginRequest`, `login` y `get_user_by_email`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.repositories import user_repository
from app.schemas.auth import LoginRequest
from app.services import auth_service
from tests.auth_support import create_auth_user
from tests.db_support import unique_suffix


def test_u1_login_request_requires_exactly_one_identifier() -> None:
    """U-1/CA-3: ni uno ni dos identificadores ⇒ error de modelo (`loc` vacio)."""
    with pytest.raises(ValidationError) as missing:
        LoginRequest(password="secreta")
    errors = missing.value.errors()
    assert [error["type"] for error in errors] == ["identifier_required"]
    assert errors[0]["loc"] == ()
    assert errors[0]["msg"] == "Indique 'username' o 'email' (exactamente uno)"

    with pytest.raises(ValidationError) as both:
        LoginRequest(email="a@b.c", username="usuario", password="secreta")
    errors = both.value.errors()
    assert [error["type"] for error in errors] == ["identifier_required"]
    assert errors[0]["loc"] == ()


def test_u2_login_request_strips_normalizes_and_honours_limits() -> None:
    """U-2/CA-2, CA-4, CA-9: `strip`, limites y mensajes en espanol."""
    stripped_email = LoginRequest(email="  a@b.c  ", password="secreta")
    assert stripped_email.email == "a@b.c"
    assert stripped_email.username is None

    stripped_username = LoginRequest(username="  usuario  ", password="secreta")
    assert stripped_username.username == "usuario"
    assert stripped_username.email is None

    with pytest.raises(ValidationError) as empty_email:
        LoginRequest(email="   ", password="secreta")
    assert empty_email.value.errors()[0]["type"] == "email_empty"
    assert empty_email.value.errors()[0]["msg"] == "El correo electrónico no puede estar vacío"

    with pytest.raises(ValidationError) as bad_email:
        LoginRequest(email="a@b", password="secreta")
    assert bad_email.value.errors()[0]["type"] == "email_format"
    assert bad_email.value.errors()[0]["msg"] == "El correo electrónico no tiene un formato válido"

    with pytest.raises(ValidationError) as long_email:
        LoginRequest(email="a" * 251 + "@b.cd", password="secreta")
    assert long_email.value.errors()[0]["loc"] == ("email",)
    assert long_email.value.errors()[0]["type"] == "string_too_long"

    with pytest.raises(ValidationError) as long_username:
        LoginRequest(username="u" * 51, password="secreta")
    assert long_username.value.errors()[0]["loc"] == ("username",)
    assert long_username.value.errors()[0]["type"] == "string_too_long"


async def test_u3_login_rejects_missing_or_duplicated_identifier() -> None:
    """U-3/CA-3: defensa de programacion del servicio antes de tocar la sesion."""
    with pytest.raises(ValueError, match="exactamente uno"):
        await auth_service.login(  # type: ignore[arg-type]
            None,
            password="secreta",
            client_ip="127.0.0.1",
        )

    with pytest.raises(ValueError, match="exactamente uno"):
        await auth_service.login(  # type: ignore[arg-type]
            None,
            username="usuario",
            email="a@b.c",
            password="secreta",
            client_ip="127.0.0.1",
        )


async def test_u4_get_user_by_email_is_case_insensitive_and_loads_role(
    db_conn: AsyncConnection, db_session: AsyncSession
) -> None:
    """U-4/CA-1, CA-2: CITEXT + `selectinload(role)` sin `MissingGreenlet`."""
    suffix = unique_suffix()
    stored = f"Repo_{suffix}@Example.Test"
    user_id = await create_auth_user(
        db_conn,
        username=f"repo_{suffix}",
        email=stored,
    )

    found = await user_repository.get_user_by_email(db_session, f"repo_{suffix}@example.test")

    assert found is not None
    assert found.id == user_id
    assert found.email == stored
    assert found.role.name == "ADMIN"
    assert (
        await user_repository.get_user_by_email(db_session, f"missing_{suffix}@example.test")
        is None
    )
