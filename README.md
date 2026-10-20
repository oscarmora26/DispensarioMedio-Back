# Dispensario Médico — Backend

API REST para la gestión de un dispensario médico. Construida con FastAPI, SQLAlchemy 2 async y PostgreSQL 17.

## Requisitos

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Docker Desktop (PostgreSQL local)

## Inicio rápido

```powershell
uv sync                                  # crea .venv e instala dependencias
Copy-Item .env.example .env              # configuración local
docker compose up -d db                  # PostgreSQL 17 en localhost:5433
uv run alembic upgrade head              # aplica migraciones
uv run fastapi dev                       # http://localhost:8000
```

Documentación interactiva: <http://localhost:8000/docs>

### Todo en contenedores

```powershell
docker compose up --build
```

## Endpoints base

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/v1/health` | Liveness. No consulta dependencias. |
| GET | `/api/v1/ready` | Readiness. Verifica PostgreSQL (`SELECT 1`). |
| GET | `/docs` | OpenAPI UI (deshabilitada en `prod`). |

Todas las respuestas incluyen el header `X-Request-ID`.

## Comandos

```powershell
uv run pytest -q                         # tests
uv run ruff check .                      # lint
uv run ruff check . --fix                # lint + autofix
uv run ruff format .                     # formato
uv run alembic revision --autogenerate -m "mensaje"
uv run alembic upgrade head
docker compose up -d db
docker compose logs -f api
```

## Estructura

```text
app/
├── main.py              # create_app(), lifespan, middlewares
├── api/v1/routes/       # endpoints
├── core/                # config, logging, exceptions
├── db/                  # engine, sesiones, Base
├── middleware/          # request id + access log
├── models/              # SQLAlchemy
└── schemas/             # Pydantic v2
alembic/                 # migraciones
tests/                   # pytest
docs/                    # ADR, specs de features, reviews
```

## Configuración

Variables en `.env` (ver `.env.example`):

| Variable | Default | Descripción |
|---|---|---|
| `ENVIRONMENT` | `local` | `local`, `dev`, `test`, `staging`, `prod` |
| `DEBUG` | `false` | Modo debug de FastAPI |
| `LOG_LEVEL` | `INFO` | Nivel de logging |
| `LOG_JSON` | `false` | Forzar salida JSON (en `prod` siempre es JSON) |
| `DATABASE_URL` | `postgresql+asyncpg://...` | Conexión de la app |
| `POSTGRES_*` | `dispensario` | Credenciales del contenedor |

## Logging

- Desarrollo: consola legible con colores.
- Producción (`prod` o `LOG_JSON=true`): una línea JSON por evento.
- Cada request lleva `request_id`; el middleware de acceso emite `request.completed` con `method`, `path`, `status` y `duration_ms`.
- Las claves sensibles (`password`, `token`, `authorization`, documento, teléfono, correo) se redactan automáticamente.
- Nunca se registran datos clínicos de pacientes.

## Flujo de trabajo del equipo

- `AGENTS.md` — convenciones, comandos y reglas del proyecto.
- `feature-list.md` — fuente de verdad del backlog y sus estados.
- `docs/features/` — especificaciones por feature.
- `docs/adr/` — decisiones de arquitectura.

Solo el humano aprueba features (`proposed → approved`) y **nadie hace `git push`**.
