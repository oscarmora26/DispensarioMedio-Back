# FEAT-019 — Permisos de visitas: ADMIN solo lectura, MEDICO todas las acciones

**Estado:** in_progress
**Fecha:** 2026-10-27
**Pedido del humano (verbatim):** "Necesito que el admin solo pueda leer las visitas y el Medico puede realizar todas la acciones en cuanto a las visitas"
**Decisiones del humano:** el ADMIN pierde toda escritura de visitas (solo `GET` listado y detalle); el MEDICO actúa **solo sobre sus propias visitas**.

## Contexto y cambio de requisito

Esta feature **modifica el requisito funcional RF-12** (*"El administrador debe poder editar y anular visitas"*, prioridad `Must` en `DispensarioMedicoDocs/01-requerimientos-y-backlog.md`) y refuerza RF-13 (*"el médico debe poder consultar únicamente sus propias visitas"*). El humano aprobó explícitamente la inversión el 2026-10-27.

Antes (FEAT-013): el ADMIN podía crear (eligiendo `doctor_id`), editar y anular; el MEDICO solo creaba y leía lo suyo.

Después (FEAT-019): el ADMIN **solo lee**; el MEDICO crea, edita y anula **sus** visitas.

## Reglas

- **R-1** `GET /visits` y `GET /visits/{id}`: `ADMIN` ve todas, `MEDICO` solo las propias (sin cambios respecto de FEAT-013).
- **R-2** `POST /visits`, `PUT /visits/{id}` y `POST /visits/{id}/annul`: **solo `MEDICO`**. El `ADMIN` recibe `403 forbidden`.
- **R-3** El médico de la visita se obtiene siempre del token. Si el body trae un `doctor_id` distinto ⇒ `422 validation_error` con `field: "doctor_id"` y mensaje `"El médico se obtiene del token"`. Ya no existe el camino "ADMIN elige `doctor_id`".
- **R-4** Un `MEDICO` sin `doctor_id` asociado no puede operar: `403 doctor_not_associated`.
- **R-5** El médico solo opera sobre sus propias visitas. Una visita ajena responde `404 visit_not_found` en detalle, `PUT` y anulación, sin revelar su existencia.
- **R-6** La comprobación de propiedad precede a la de estado: una visita anulada de otro médico ⇒ `404`, no `409`.
- **R-7** La anulación conserva el motivo obligatorio y la auditoría (`annulled_by`, `annulled_at`, `annulment_reason`); ahora el actor es el médico dueño.
- **R-8** Sin cambios de esquema ni migraciones: `annulled_by` y `created_by` ya referencian `users.id`.

## Cambios

| Archivo | Cambio |
|---|---|
| `app/api/v1/routes/visits.py` | `MedicoUser` (`require_role("MEDICO")`) en `POST`, `PUT` y `annul`; descripciones actualizadas |
| `app/services/visit_service.py` | `_ensure_ownership` compartido por detalle, `PUT` y anulación; `_resolve_doctor` simplificado al camino del token |

## Criterios de aceptación

- [x] **CA-1** `ADMIN` recibe `403 forbidden` en `POST /visits`, `PUT /visits/{id}` y `POST /visits/{id}/annul`.
- [x] **CA-2** `ADMIN` sigue leyendo el listado completo y el detalle.
- [x] **CA-3** `MEDICO` crea, edita y anula sus propias visitas (`201`/`200`).
- [x] **CA-4** `MEDICO` sobre visita ajena ⇒ `404 visit_not_found` en detalle, `PUT` y anulación.
- [x] **CA-5** `doctor_id` distinto del token ⇒ `422` con `field: "doctor_id"`.
- [x] **CA-6** `MEDICO` sin doctor asociado ⇒ `403 doctor_not_associated`.
- [x] **CA-7** Visita anulada ajena ⇒ `404` (la propiedad precede al estado).
- [x] **CA-8** Suite completa verde con los tests migrados al nuevo contrato: **741 passed**.

## Nota sobre el modelo de autorización

`require_role("MEDICO")` sigue usando nombres de rol literales, igual que el resto de la API. Si en el futuro se suman roles (por ejemplo `CONSULTOR`), corresponde migrar a `require_permission`; queda fuera del alcance de esta feature.

## Verificacion final

- Suite: `DATABASE_URL=...5434/dispensario_test uv run pytest -q` -> **741 passed**.
- `ruff check .` y `ruff format --check .` limpios; `alembic check` sin diferencias (sin migracion).
- Smoke por API: ADMIN `403` en `POST`/`PUT`/`annul` y `200` en listado y detalle; MEDICO `201`/`200` en las propias;
  MEDICO `404 visit_not_found` en detalle, `PUT` y anulacion de una visita ajena (incluida una anulada);
  `doctor_id` distinto del token -> `422` con `field: "doctor_id"`.
- Corregida la descripcion del campo `doctor_id` en `app/schemas/visit.py`, que aun decia "obligatorio para ADMIN".
