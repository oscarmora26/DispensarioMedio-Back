"""Rate limiter en memoria para el login, por proceso."""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from app.core.config import get_settings

MAX_TRACKED_KEYS = 1024


def build_key(ip: str, username: str) -> str:
    """Construye la clave `(IP, usuario normalizado)` del limitador."""
    return f"{ip}|{username.strip().casefold()}"


class LoginRateLimiter:
    """Ventana deslizante en memoria de intentos fallidos de login."""

    def __init__(
        self,
        max_attempts: int = 5,
        window_seconds: int = 900,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts debe ser mayor que cero")
        if window_seconds < 1:
            raise ValueError("window_seconds debe ser mayor que cero")
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._clock = clock or time.monotonic
        self._failures: dict[str, list[float]] = {}

    def _now(self) -> float:
        return self._clock()

    def _prune(self, key: str, now: float) -> list[float]:
        """Descarta los fallos fuera de la ventana y elimina la clave si queda vacia."""
        timestamps = self._failures.get(key)
        if timestamps is None:
            return []
        cutoff = now - self.window_seconds
        active = [timestamp for timestamp in timestamps if timestamp > cutoff]
        if active:
            self._failures[key] = active
        else:
            self._failures.pop(key, None)
        return active

    def retry_after(self, key: str) -> int | None:
        """Segundos hasta liberar la clave, o `None` si aun se permiten intentos."""
        now = self._now()
        timestamps = self._prune(key, now)
        if len(timestamps) < self.max_attempts:
            return None
        remaining = self.window_seconds - (now - timestamps[0])
        return max(1, math.ceil(remaining))

    def check(self, key: str) -> int | None:
        """Alias explicito de `retry_after` para el flujo de login."""
        return self.retry_after(key)

    def is_limited(self, key: str) -> bool:
        """Indica si la clave ya consume todos los intentos de la ventana."""
        return self.retry_after(key) is not None

    def record_failure(self, key: str) -> None:
        """Registra un intento fallido y purga el mapa si crece demasiado."""
        now = self._now()
        timestamps = self._prune(key, now)
        timestamps.append(now)
        self._failures[key] = timestamps
        if len(self._failures) > MAX_TRACKED_KEYS:
            self._purge(now)

    def register_failure(self, key: str) -> None:
        """Alias de `record_failure`."""
        self.record_failure(key)

    def _purge(self, now: float) -> None:
        """Elimina todas las claves que no tienen fallos vigentes."""
        cutoff = now - self.window_seconds
        self._failures = {
            key: timestamps
            for key, timestamps in self._failures.items()
            if any(timestamp > cutoff for timestamp in timestamps)
        }

    def reset(self, key: str) -> None:
        """Borra el estado de una sola clave."""
        self._failures.pop(key, None)

    def reset_all(self) -> None:
        """Borra el estado de todas las claves."""
        self._failures.clear()

    def configure(self, max_attempts: int, window_seconds: int) -> None:
        """Reconfigura el limite y reinicia el estado."""
        if max_attempts < 1:
            raise ValueError("max_attempts debe ser mayor que cero")
        if window_seconds < 1:
            raise ValueError("window_seconds debe ser mayor que cero")
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.reset_all()

    def __len__(self) -> int:
        return len(self._failures)


_limiter: LoginRateLimiter | None = None
_limiter_settings: tuple[int, int] | None = None


def get_login_rate_limiter() -> LoginRateLimiter:
    """Devuelve el limitador singleton del proceso, configurado con los settings.

    Si los settings de rate limit cambian (por ejemplo tras `cache_clear()` y
    `monkeypatch.setenv`), el singleton se reconfigura y reinicia su estado.
    Las llamadas a `configure()` del propio limitador no se pisan.
    """
    global _limiter, _limiter_settings
    settings = get_settings()
    snapshot = (
        settings.login_rate_limit_max_attempts,
        settings.login_rate_limit_window_seconds,
    )
    if _limiter is None:
        _limiter = LoginRateLimiter(max_attempts=snapshot[0], window_seconds=snapshot[1])
        _limiter_settings = snapshot
    elif snapshot != _limiter_settings:
        _limiter.configure(max_attempts=snapshot[0], window_seconds=snapshot[1])
        _limiter_settings = snapshot
    return _limiter
