# FEAT-014 — Reporte de visitas (JSON/CSV) y exportación

Puntero ODD. La autoridad funcional completa es `docs/features/FEAT-014.md` (leerla entera antes de escribir código). Feature aprobada por el humano el 2026-10-24 en bloque FEAT-011…014 (historial en `feature-list.md`).

## Specs

- S1: Petición humana original (2026-10-24 00:35): "Feature: Completar la implementacion del la definicion definida en los siguientes archivos: C:\Users\oscar\source\repos\IA\DispensarioMedicoDocs\docs"
- S2: `docs/features/FEAT-014.md` — reglas 1–18, decisiones D-1…D-8, CA-1…CA-18, contrato de repositorio y de API. Cita esta spec antes que este puntero.
- S3: Fuentes del documento de API citadas textualmente en la spec: `03-api-rest.md` §12 y `01-requerimientos-y-backlog.md` US-35/US-36, RF-16/RF-21.

## Tasks

- T1: Implementar FEAT-014 completo (schemas, `summarize_visits` en el repositorio, servicio de reporte, rutas, registro en router) con sus tests (CA-1…CA-18) según `docs/features/FEAT-014.md`. Sin migraciones. Sin commits (los hace el team-lead).
- T2: Verificar gates: tests enfocados de reports en verde, `ruff check app/ tests/`, `ruff format --check app/ tests/`, `alembic check`.

## Log

- L1 (2026-10-24 00:35): "Feature: Completar la implementacion del la definicion definida en los siguientes archivos: C:\Users\oscar\source\repos\IA\DispensarioMedicoDocs\docs"
- L2 (2026-10-24): El humano aprobó FEAT-011…014 en secuencia continua (ver `feature-list.md`, historial).
- L3 (2026-10-24): FEAT-011, FEAT-012 y FEAT-013 `done` (APPROVED). FEAT-014 `in_progress` con esta spec.
