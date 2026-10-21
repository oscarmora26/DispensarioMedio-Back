"""Tests unitarios del rate limiter del login (FEAT-002, CA-32)."""

from __future__ import annotations

import pytest

from app.core.rate_limit import MAX_TRACKED_KEYS, LoginRateLimiter, build_key


class FakeClock:
    """Reloj determinista controlado por el test."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_ca32_retry_after_only_when_limit_reached() -> None:
    """CA-32: 4 fallos no bloquean, el 5.º bloquea con `retry_after >= 1`."""
    clock = FakeClock()
    limiter = LoginRateLimiter(max_attempts=5, window_seconds=900, clock=clock)
    key = build_key("1.2.3.4", "admin")

    for _ in range(4):
        limiter.record_failure(key)
    assert limiter.retry_after(key) is None
    assert limiter.is_limited(key) is False

    limiter.record_failure(key)
    retry_after = limiter.retry_after(key)
    assert retry_after is not None
    assert retry_after >= 1


def test_ca32_window_expiry_releases_key() -> None:
    """CA-32: al avanzar la ventana + 1 la clave queda libre y se elimina."""
    clock = FakeClock()
    limiter = LoginRateLimiter(max_attempts=5, window_seconds=900, clock=clock)
    key = build_key("1.2.3.4", "admin")

    for _ in range(5):
        limiter.record_failure(key)
    assert limiter.is_limited(key) is True
    assert len(limiter) == 1

    clock.advance(901)
    assert limiter.retry_after(key) is None
    assert len(limiter) == 0


def test_ca32_reset_only_affects_target_key() -> None:
    """CA-32: `reset(key)` libera una clave y deja el resto intacto."""
    clock = FakeClock()
    limiter = LoginRateLimiter(max_attempts=5, window_seconds=900, clock=clock)
    first = build_key("1.2.3.4", "admin")
    second = build_key("1.2.3.4", "medico")

    for key in (first, second):
        for _ in range(5):
            limiter.record_failure(key)

    limiter.reset(first)

    assert limiter.retry_after(first) is None
    assert limiter.is_limited(second) is True


def test_ca32_reset_all_empties_state() -> None:
    """CA-32: `reset_all()` vacia todas las claves."""
    clock = FakeClock()
    limiter = LoginRateLimiter(max_attempts=2, window_seconds=60, clock=clock)
    for index in range(3):
        limiter.record_failure(build_key("10.0.0.1", f"user{index}"))

    assert len(limiter) == 3

    limiter.reset_all()

    assert len(limiter) == 0


def test_ca32_purges_keys_without_active_failures() -> None:
    """CA-32: superar 1024 claves purga las que no tienen fallos vigentes."""
    clock = FakeClock()
    limiter = LoginRateLimiter(max_attempts=5, window_seconds=900, clock=clock)

    for index in range(MAX_TRACKED_KEYS + 76):
        limiter.record_failure(build_key("10.0.0.1", f"user{index}"))
    assert len(limiter) == MAX_TRACKED_KEYS + 76

    clock.advance(901)
    limiter.record_failure(build_key("10.0.0.1", "nuevo"))

    assert len(limiter) == 1


def test_ca32_build_key_normalizes_username_only() -> None:
    """CA-32: la clave normaliza el usuario y conserva la IP tal cual."""
    assert build_key("1.2.3.4", " Admin ") == build_key("1.2.3.4", "admin")
    assert build_key("1.2.3.4", "admin") == "1.2.3.4|admin"
    assert build_key("::1", "Admin") != build_key("1.2.3.4", "Admin")


def test_limiter_rejects_non_positive_configuration() -> None:
    """Guardia defensiva: el limitador no acepta limites no positivos."""
    with pytest.raises(ValueError):
        LoginRateLimiter(max_attempts=0)
    with pytest.raises(ValueError):
        LoginRateLimiter(max_attempts=5, window_seconds=0)
