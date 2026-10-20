# ADR 0001 — Stack del backend

**Estado:** aceptado
**Fecha:** 2026-10-20

## Contexto

Backend de un dispensario médico: datos relacionales, historias clínicas, inventario y recetas. Requiere consistencia transaccional, trazabilidad de accesos y despliegue reproducible.

## Decisión

| Área | Elección | Motivo |
|---|---|---|
| Framework | FastAPI 0.150+ | Tipado, OpenAPI automático, async nativo |
| Runtime | Python 3.12 | Soportado por todas las dependencias, estable |
| ORM | SQLAlchemy 2.x async | Control de transacciones, `SELECT FOR UPDATE`, ecosistema maduro |
| Driver | asyncpg | Mejor rendimiento para PostgreSQL en async |
| Migraciones | Alembic | Estándar del ecosistema SQLAlchemy, `autogenerate` |
| DB | PostgreSQL 17 | Integridad referencial, tipos ricos, `JSONB`, auditoría |
| Validación | Pydantic v2 + pydantic-settings | Rendimiento y ergonomía |
| Logs | structlog + stdlib bridge | JSON estructurado, contexto por request |
| Tooling | uv | Reproducibilidad (`uv.lock`), velocidad, un solo binario |
| Contenedores | Docker + Compose | Paridad local/producción |

## Alternativas consideradas

- **SQLModel**: menor boilerplate, pero menos control sobre queries, relaciones y migraciones. Se descarta para un dominio con historias clínicas y auditoría.
- **Tortoise ORM**: sintaxis sencilla, pero menor madurez en transacciones complejas.
- **venv + pip**: funcional, pero sin lockfile determinista y mucho más lento en CI.
- **Loguru**: simple, pero structlog se integra mejor con el logging estándar y con el contexto asíncrono.

## Consecuencias

- El equipo debe instalar `uv` y Docker.
- Toda query es async: prohibido IO bloqueante en el camino HTTP.
- Los cambios de esquema pasan siempre por una migración de Alembic.
- La configuración de logging debe mantener la redacción de datos sensibles; cualquier logger nuevo hereda el pipeline raíz.
