# AGENTS.md

Convenciones y comandos del backend del dispensario médico. Todo agente (y humano) que trabaje en este repo debe leerlo antes de tocar código.

## Stack

| Componente | Versión / decisión |
|---|---|
| Python | 3.12 (`requires-python = ">=3.12,<3.13"`) |
| Framework | FastAPI 0.150+ con `fastapi dev` / `fastapi run` |
| ORM | SQLAlchemy 2.x async + `asyncpg` |
| Migraciones | Alembic (template async) |
| Validación | Pydantic v2 + `pydantic-settings` |
| Base de datos | PostgreSQL 17 (Docker) |
| Logs | structlog → JSON en producción, consola en local |
| Tests | pytest + pytest-asyncio + httpx |
| Lint/format | Ruff |
| Tooling | uv (lockfile `uv.lock`) |
| Contenedores | Docker + Docker Compose |

Decisiones ampliadas en `docs/adr/`.

## Comandos

```powershell
uv sync                      # instalar/actualizar el entorno
uv run fastapi dev           # servidor de desarrollo con reload
uv run fastapi run           # servidor "producción" local
uv run pytest -q             # tests
uv run ruff check .          # lint
uv run ruff check . --fix    # lint + autofix
uv run ruff format .         # formato
uv run alembic upgrade head  # aplicar migraciones
uv run alembic revision --autogenerate -m "mensaje"
docker compose up -d db      # PostgreSQL local
docker compose up --build    # API + DB en contenedores
```

## Estructura

```text
app/
├── main.py                  # create_app(), lifespan, wiring
├── api/v1/routes/           # endpoints (sin lógica de negocio)
├── core/                    # config, logging, exceptions, seguridad
├── db/                      # engine, sesiones, Base declarativa
├── middleware/              # request id, access log
├── models/                  # SQLAlchemy (Mapped[])
├── schemas/                 # Pydantic v2
├── repositories/            # acceso a datos (se crea al necesitarlo)
└── services/                # reglas de negocio (se crea al necesitarlo)
alembic/                     # migraciones
tests/                       # unitarios e integración
docs/
├── adr/                     # decisiones de arquitectura
├── features/                # especificaciones por feature
└── reviews/                 # informes del reviewer
```

Flujo de dependencias: `routes → services → repositories → models`. Un router no consulta la base directamente; un repositorio no conoce HTTP.

## Convenciones de código

- **FastAPI**: `Annotated[..., Depends(...)]` siempre; sin `...` como valor por defecto; `response_model` solo si difiere del tipo de retorno; routers con `prefix` y `tags`.
- **Async**: todo el camino HTTP es `async`; no bloquear el event loop (nada de `requests`, `time.sleep` ni IO síncrono).
- **SQLAlchemy**: `Mapped[...]` + `mapped_column`, `Base` de `app/db/base.py`, sesión vía `get_session`. Nada de `session.query()`, usar `select()`.
- **Pydantic**: schemas de entrada/salida separados de los modelos ORM; validadores explícitos; sin `dict()` (usar `model_dump()`).
- **Errores**: `AppError`, `NotFoundError`, `ConflictError` (`app/core/exceptions.py`). `HTTPException` solo en routers para casos triviales de auth/permiso.
- **Nombres**: tablas y columnas en `snake_case`; eventos de log en `snake_case` (`patient.created`); schemas `XxxCreate`, `XxxUpdate`, `XxxRead`.
- **Tipado**: anotaciones completas en firmas públicas; `Any` solo cuando sea inevitable.
- **Idioma**: código e identificadores en inglés; mensajes de negocio y documentación en español.

## Datos y seguridad

- Ningún dato de paciente (nombre, documento, diagnóstico, teléfono, correo) se registra en logs. El logger aplica redacción sobre claves sensibles, pero no confíes solo en eso.
- Secretos únicamente por variables de entorno; `.env` está fuera de git. `.env.example` documenta las claves.
- Endpoints nuevos requieren autenticación y autorización explícitas.
- Migraciones destructivas (`DROP`, `ALTER TYPE`, borrado de columnas) requieren aprobación humana en la spec.
- Dinero: `Numeric(12, 2)`, nunca `float`. Fechas en UTC (`DateTime(timezone=True)`).

## Flujo de trabajo (obligatorio)

1. `feature-list.md` es la única fuente de verdad del backlog.
2. Estados: `proposed`, `approved`, `in_progress`, `in_review`, `changes_requested`, `done`, `blocked`, `rejected`.
3. **Necesitas confirmacion del humano para mover de estos estados** `proposed → approved`.
4. El team-lead ejecuta únicamente features `approved`.
5. **Una feature a la vez**: nada de trabajo paralelo sobre el mismo código.
6. Solo el team-lead escribe estados en `feature-list.md`.
7. El reviewer no modifica código de producción; solo `tests/**` y `docs/reviews/**`.
8. **Nadie hace `git push`.** Los commits locales están permitidos; el push y los PR los hace el humano.

Roles:

| Agente | Responsabilidad | Puede editar |
|---|---|---|
| `team-lead` | Orquesta, actualiza estados, delega | `feature-list.md`, `docs/**` |
| `spec-author` | Especificaciones verificables | `docs/features/**` |
| `implementer` | Código de producción y migraciones | `app/**`, `alembic/**`, build |
| `reviewer` | Tests y revisión | `tests/**`, `docs/reviews/**` |

## Git

- Commits convencionales: `feat(FEAT-NNN): ...`, `fix(FEAT-NNN): ...`, `docs: ...`, `chore: ...`.
- Un commit por feature cerrada (o por cambio coherente).
- Prohibido: `git push`, `git push --force`, `gh pr merge`. Está bloqueado por configuración.
