# FEAT-001 — Fundaciones de la API

**Estado:** `done` (bootstrap por instrucción directa del humano)
**Fecha:** 2026-10-20
**Alcance:** línea base técnica, no funcionalidad de negocio.

## Contexto

El repo estaba vacío. Se necesitaba una base ejecutable sobre la que el equipo pueda implementar features de negocio con convenciones y guardarraíles desde el primer commit.

## Alcance

- Proyecto Python 3.12 gestionado con `uv` (`pyproject.toml` + `uv.lock`).
- FastAPI 0.150 con entrypoint `app.main:app` y CLI `fastapi dev|run`.
- Configuración tipada por entorno (`pydantic-settings`).
- Logging estructurado con `structlog`: JSON en producción, consola en desarrollo, `request_id` por request, redacción de claves sensibles y log de acceso propio.
- SQLAlchemy 2.x async + `asyncpg`; `Base` con convención de nombres; sesión por request.
- Alembic en modo async, URL tomada de `DATABASE_URL`.
- Endpoints `GET /api/v1/health` (liveness) y `GET /api/v1/ready` (readiness con `SELECT 1`).
- PostgreSQL 17 en Docker Compose + Dockerfile multi-stage con `uv`.
- Tests base con `pytest`, `pytest-asyncio` y `httpx.ASGITransport`.
- Convenciones del proyecto en `AGENTS.md` y decisiones en `docs/adr/`.

## Fuera de alcance

- Autenticación y autorización (FEAT-002).
- Modelos de dominio (pacientes, medicamentos, inventario, recetas, citas).
- CI/CD.

## Criterios de aceptación (verificados)

- [x] `uv sync` deja el entorno reproducible a partir de `uv.lock`.
- [x] `uv run fastapi dev` levanta la app y `GET /api/v1/health` responde `200` con `status=ok`.
- [x] `GET /api/v1/ready` responde `200`/`503` según el estado de PostgreSQL.
- [x] Toda respuesta incluye `X-Request-ID` y los logs comparten ese identificador.
- [x] Los logs de acceso incluyen método, ruta, status y `duration_ms`.
- [x] `uv run pytest -q` pasa en verde.
- [x] `uv run ruff check .` sin errores.
- [x] `docker compose up -d db` levanta PostgreSQL 17 con healthcheck.
- [x] `uv run alembic upgrade head` funciona contra la base de compose.
- [x] `git push` está bloqueado por configuración (permissions + policies).

## Evidencia

- `uv run pytest -q` → 4 passed.
- `uv run ruff check .` → limpio.
- Migraciones: línea base vacía (sin modelos todavía).

## Historial

- 2026-10-20 — creada y ejecutada como bootstrap por instrucción del humano.
