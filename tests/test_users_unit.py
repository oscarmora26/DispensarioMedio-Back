"""Pruebas unitarias de FEAT-004 (U-1 a U-8)."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.exceptions import AppError
from app.repositories import user_repository
from app.repositories.user_repository import escape_like
from app.schemas.user import (
    SORT_FIELDS,
    SORT_PATTERN,
    UserCreate,
    UserRead,
    UserResetPasswordRequest,
    UserUpdate,
)
from app.services import user_service


def test_u1_user_create_validates_limits_and_normalizes() -> None:
    """U-1: limites, formatos, defaults, `strip` y capitalizacion."""
    user = UserCreate(
        username="  Abcd  ",
        email="  A@B.CO  ",
        password="  abcd1234  ",
        role_id=2,
    )
    assert user.username == "Abcd"
    assert user.email == "A@B.CO"
    assert user.password == "  abcd1234  "
    assert user.doctor_id is None
    assert user.is_active is True

    invalid = [
        {"username": "abc", "email": "a@b.co", "password": "x" * 8, "role_id": 2},
        {"username": "a" * 51, "email": "a@b.co", "password": "x" * 8, "role_id": 2},
        {"username": "ab cd", "email": "a@b.co", "password": "x" * 8, "role_id": 2},
        {"username": "  abc  ", "email": "a@b.co", "password": "x" * 8, "role_id": 2},
        {"username": "abcd", "email": "sin-arroba", "password": "x" * 8, "role_id": 2},
        {
            "username": "abcd",
            "email": f"{'x' * 250}@example.test",
            "password": "x" * 8,
            "role_id": 2,
        },
        {"username": "abcd", "email": "a@b.co", "password": "x" * 7, "role_id": 2},
        {"username": "abcd", "email": "a@b.co", "password": "x" * 129, "role_id": 2},
    ]
    for payload in invalid:
        with pytest.raises(ValidationError):
            UserCreate(**payload)

    assert UserCreate(username="abcd", email="a@b.co", password="x" * 8, role_id=2).password
    assert UserCreate(username="abcd", email="a@b.co", password="x" * 128, role_id=2).password


def test_u2_user_update_requires_all_five_fields() -> None:
    """U-2: `UserUpdate` exige los cinco campos y acepta `doctor_id=None`."""
    complete = UserUpdate(
        username="abcd", email="a@b.co", role_id=2, doctor_id=None, is_active=True
    )
    assert complete.doctor_id is None
    assert complete.is_active is True

    base = {
        "username": "abcd",
        "email": "a@b.co",
        "role_id": 2,
        "doctor_id": None,
        "is_active": True,
    }
    for field in base:
        payload = {key: value for key, value in base.items() if key != field}
        with pytest.raises(ValidationError) as excinfo:
            UserUpdate(**payload)
        assert any(error["loc"][0] == field for error in excinfo.value.errors())


def test_u3_reset_password_schema_bounds_without_strip() -> None:
    """U-3: `new_password` 8..128 y sin `strip`."""
    assert UserResetPasswordRequest(new_password="x" * 8).new_password == "x" * 8
    assert UserResetPasswordRequest(new_password="x" * 128).new_password == "x" * 128
    spaced = "  abcd12  "
    assert UserResetPasswordRequest(new_password=spaced).new_password == spaced

    for value in ("x" * 7, "x" * 129):
        with pytest.raises(ValidationError):
            UserResetPasswordRequest(new_password=value)


def test_u4_user_read_serializes_exactly_six_keys() -> None:
    """U-4: `UserRead` solo expone las seis claves de D-4."""
    read = UserRead(
        id=1, username="abcd", email="a@b.co", role="MEDICO", doctor_id=None, is_active=True
    )
    assert set(read.model_dump()) == {
        "id",
        "username",
        "email",
        "role",
        "doctor_id",
        "is_active",
    }


def test_u5_sort_catalog_accepts_ten_variants_and_rejects_others() -> None:
    """U-5: catalogo de `sort` (10 variantes) y rechazo de invalidos."""
    pattern = re.compile(SORT_PATTERN)
    for field in SORT_FIELDS:
        assert pattern.fullmatch(field)
        assert pattern.fullmatch(f"-{field}")
        assert user_service.parse_sort(field) == (field, False)
        assert user_service.parse_sort(f"-{field}") == (field, True)

    for invalid in ("", "password_hash", "username,email", "-", "USERNAME"):
        assert pattern.fullmatch(invalid) is None
        with pytest.raises(AppError) as excinfo:
            user_service.parse_sort(invalid)
        error = excinfo.value
        assert error.code == "validation_error"
        assert error.status_code == 422
        assert error.errors == [{"field": "sort", "message": "Valor de orden no permitido"}]


def test_u6_escape_like_turns_wildcards_into_literals() -> None:
    """U-6: `%`, `_` y `\\` se escapan para `ILIKE ... ESCAPE`."""
    assert escape_like("100%") == "100\\%"
    assert escape_like("a_b") == "a\\_b"
    assert escape_like("c\\d") == "c\\\\d"
    assert escape_like("%_\\") == "\\%\\_\\\\"


def test_u7_calculate_pages() -> None:
    """U-7: `pages` es 0 sin filas y el techo en el resto."""
    assert user_service.calculate_pages(0, 20) == 0
    assert user_service.calculate_pages(1, 20) == 1
    assert user_service.calculate_pages(20, 20) == 1
    assert user_service.calculate_pages(21, 20) == 2
    assert user_service.calculate_pages(101, 100) == 2


async def test_u8_service_checks_role_before_doctor_and_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """U-8: rol → medico → coherencia → auto-desactivacion → ultimo admin → duplicados."""
    calls: list[str] = []

    async def fake_role(session, role_id):
        calls.append("role")
        return None

    async def fake_doctor(session, doctor_id):
        calls.append("doctor")
        return SimpleNamespace(id=doctor_id)

    async def fake_duplicates(session, *, username, email, exclude_id=None):
        calls.append("duplicates")
        return None

    async def fake_doctor_owner(session, doctor_id, *, exclude_id=None):
        calls.append("doctor_owner")
        return None

    async def fake_get_for_update(session, user_id):
        calls.append("load_user")
        return SimpleNamespace(id=user_id, is_active=True, role=SimpleNamespace(name="ADMIN"))

    monkeypatch.setattr(user_repository, "get_role_by_id", fake_role)
    monkeypatch.setattr(user_repository, "find_user_by_username_or_email", fake_duplicates)
    monkeypatch.setattr(user_repository, "find_user_by_doctor_id", fake_doctor_owner)
    monkeypatch.setattr(user_repository, "get_user_by_id_for_update", fake_get_for_update)
    monkeypatch.setattr("app.repositories.doctor_repository.get_doctor_by_id", fake_doctor)

    create_payload = UserCreate(
        username="abcd", email="a@b.co", password="x" * 8, role_id=999, doctor_id=7
    )
    with pytest.raises(AppError) as create_error:
        await user_service.create_user(
            SimpleNamespace(), actor=SimpleNamespace(id=1), payload=create_payload
        )
    assert create_error.value.code == "invalid_role"
    assert calls == ["role"]

    calls.clear()
    update_payload = UserUpdate(
        username="abcd", email="a@b.co", role_id=999, doctor_id=7, is_active=False
    )
    with pytest.raises(AppError) as update_error:
        await user_service.update_user(
            SimpleNamespace(),
            actor=SimpleNamespace(id=1),
            user_id=2,
            payload=update_payload,
        )
    assert update_error.value.code == "invalid_role"
    assert calls == ["load_user", "role"]
