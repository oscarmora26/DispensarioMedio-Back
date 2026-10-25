# FEAT-013 — Visitas (registro, listado, detalle, edición y anulación)

Puntero ODD. La autoridad funcional completa es `docs/features/FEAT-013.md` (leerla entera antes de escribir código). Feature aprobada por el humano el 2026-10-24 en bloque FEAT-011…014 (historial en `feature-list.md`).

## Specs

- S1: Petición humana original (2026-10-24 00:35): "Feature: Completar la implementacion del la definicion definida en los siguientes archivos: C:\Users\oscar\source\repos\IA\DispensarioMedicoDocs\docs"
- S2: `docs/features/FEAT-013.md` — reglas 1–37, decisiones D-1…D-13, CA-1…CA-36, contrato de repositorio y de API. Cita esta spec antes que este puntero.
- S3: Fuentes del documento de API citadas textualmente en la spec: `03-api-rest.md` §11 y `01-requerimientos-y-backlog.md` US-30/US-31/US-32, RF-11…RF-14.

## Tasks

- T1: Implementar FEAT-013 completo (schemas, repository, service, routes, registro en router) con sus tests (CA-1…CA-36) según `docs/features/FEAT-013.md`. Sin migraciones (`alembic check` debe quedar limpio). Sin commits (los hace el team-lead).
- T2: Verificar gates: tests enfocados de visits en verde, `ruff check app/ tests/`, `ruff format --check app/ tests/`, `alembic check`.

## Log

- L1 (2026-10-24 00:35): "Feature: Completar la implementacion del la definicion definida en los siguientes archivos: C:\Users\oscar\source\repos\IA\DispensarioMedicoDocs\docs"
- L2 (2026-10-24): El humano aprobó FEAT-011…014 en secuencia continua (ver `feature-list.md`, historial).
- L3 (2026-10-24): FEAT-011 y FEAT-012 `done` (APPROVED). FEAT-013 `in_progress` con esta spec.
