# FEAT-017 — Ubicaciones con estantes (shelves) y celdas (cells)

**Estado:** in_progress
**Fecha:** 2026-10-27
**Pedido del humano (verbatim):** "Quiero que modifique el apartado de ubicaciones para que funcione con Shelves y cells"
**Decisiones del humano:** reemplazo total de `locations` (migración destructiva aprobada) y reemplazo de `/api/v1/locations` por `/api/v1/shelves`.

## Contexto

`locations` era una tabla plana con `description/shelf/section/cell`. El modelo de dominio pedido separa el estante (`shelves`) de la celda (`cells`), de modo que un estante agrupa N celdas y cada medicamento apunta a una celda.

```
shelves (id, name, description, is_active, created_at, updated_at)
   └── 1:N ──> cells (id, shelf_id, section, cell, description, is_active, ...)
                   └── 1:N ──> medications.location_id
```

## Reglas

- **R-1** `shelves.name` es unico (CITEXT, case-insensitive).
- **R-2** La posicion de la celda es unica dentro del estante: `uq_cells_position (shelf_id, section, cell)`.
- **R-3** `medications.location_id` conserva su nombre pero referencia `cells.id` (`ON DELETE RESTRICT`, NOT NULL).
- **R-4** Lectura de `/shelves`: `ADMIN` y `MEDICO`. Escritura: solo `ADMIN`.
- **R-5** Listado paginado con `search` (insensible a acentos sobre `name` y `description`), `is_active` y `sort`.
- **R-6** `POST /shelves` crea estante y celdas en un solo body.
- **R-7** `PUT /shelves/{id}` sincroniza celdas por `(section, cell)` case-insensitive: las existentes se actualizan **en el lugar conservando su `id`** (es la FK de `medications`), las nuevas se crean y las ausentes se eliminan.
- **R-8** Una celda con medicamentos no se puede eliminar: `409 cell_has_medications`.
- **R-9** `DELETE /shelves/{id}` es baja logica: desactiva estante y sus celdas.
- **R-10** `description` vacia o solo espacios se normaliza a `NULL`.
- **R-11** Posiciones duplicadas en el payload (case-insensitive) => `422 validation_error` antes de tocar la base.
- **R-12** Toda violacion de integridad no contemplada responde el envelope `{detail, code, errors}` (`409 integrity_error`), nunca un traceback; cualquier excepcion no controlada responde `500 internal_error` con el mismo envelope.

## Cambios

| Archivo | Cambio |
|---|---|
| `app/models/shelf.py` | nuevo modelo `Shelf` |
| `app/models/cell.py` | nuevo modelo `Cell` |
| `app/models/medication.py` | `location_id` -> FK `cells.id`; relacion `cell` |
| `app/models/location.py` | eliminado |
| `alembic/versions/f39f66eed513_shelves_cells_schema.py` | crea `shelves`/`cells`, migra datos de `locations`, reapunta la FK de `medications`, elimina `locations` |
| `app/schemas/shelf.py` | `ShelfCreate/Update/Read/ListResponse`, `CellCreate/Update/Read` |
| `app/repositories/shelf_repository.py`, `app/repositories/cell_repository.py` | acceso a datos |
| `app/services/shelf_service.py` | reglas y sincronizacion de celdas |
| `app/api/v1/routes/shelves.py` | endpoints `/shelves` |
| `app/core/exceptions.py` | handlers de `IntegrityError` y de excepcion no controlada |
| `app/repositories/location_repository.py`, `app/services/location_service.py`, `app/schemas/location.py`, `app/api/v1/routes/locations.py` | eliminados |

## Criterios de aceptacion

- [x] **CA-1** `POST /shelves` crea estante con celdas anidadas y las devuelve en la respuesta.
- [x] **CA-2** `GET /shelves` lista estantes con sus celdas anidadas.
- [x] **CA-3** `PUT /shelves/{id}` sobre un estante cuya celda tiene medicamentos **no** produce error 500 ni deja `location_id` en `NULL`.
- [x] **CA-4** `PUT` que conserva la posicion de una celda preserva su `id` (los medicamentos siguen asociados).
- [x] **CA-5** `PUT` que elimina una celda con medicamentos responde `409 cell_has_medications` con envelope.
- [x] **CA-6** `description: ""` se almacena como `NULL`.
- [x] **CA-7** Posiciones duplicadas en el payload responden `422`.
- [x] **CA-8** Una violacion de integridad responde `409` con envelope JSON, nunca un traceback.
- [x] **CA-9** La suite completa queda verde con los modulos de test migrados a shelves/cells: **688 passed**. Tests migrados; quedan 4 fallos cuyo origen es codigo de produccion, ver *Estado de la suite*.

## Estado de la suite (2026-10-27)

Modulos de test migrados (los de `locations` fueron eliminados):

| Archivo | Contenido |
|---|---|
| `tests/shelves_support.py` | helpers de estantes y celdas (`SHELVES_PATH`, `admin_actor`, `create_shelf_with_cells`, `create_medication_for_cell`, `cell_positions`, envelopes) |
| `tests/test_shelves_authz.py` | R-4: lectura ADMIN/MEDICO, escritura solo ADMIN, matriz de autenticacion y 404 del detalle |
| `tests/test_shelves_crud.py` | CA-1 a CA-7 y R-1/R-6 a R-11, incluidas las regresiones del bugfix (ids de celda preservados, `409 cell_has_medications` sin `NULL`, `description` vacia a `NULL`, posiciones duplicadas) |
| `tests/test_shelves_integrity.py` | CA-8, envelope uniforme y contrato OpenAPI de `/api/v1/shelves` |
| `tests/test_shelves_list.py` | R-5: paginacion, `search` con `unaccent` y comodines literales, `is_active`, `sort` |
| `tests/test_shelves_unit.py` | schemas `Shelf*`/`Cell*`, `escape_like` y `calculate_pages` sin base de datos |

Tambien se actualizaron `tests/db_support.py` (`create_shelf`/`create_cell` en lugar de `create_location`), `tests/medications_support.py`, `tests/brands_support.py`, `tests/drug_types_support.py`, `tests/test_medications_*.py` (campo `cell_id`), `tests/test_db_catalog.py`, `tests/test_db_constraints.py`, `tests/test_models_metadata.py` (12 tablas, 9 revisiones, `uq_shelves_name`/`uq_cells_position`, FK `fk_medications_cell_id_cells`), `tests/test_db_migrations.py` y `tests/test_search_unaccent.py`.

Resultado inicial: `684 passed, 4 failed`. Los cuatro fallos restantes **no** eran de los tests, sino de codigo de produccion (corregidos, ver seccion siguiente):

1. `test_ca18_alembic_check_reports_no_new_operations` y `test_ca20_after_roundtrip_alembic_check_is_still_clean`: `alembic check` detecta `remove_index ix_cells_shelf_id` porque `alembic/versions/f39f66eed513_shelves_cells_schema.py` crea `ix_cells_shelf_id` pero `app/models/cell.py` no declara ese `Index`.
2. `test_ca19_downgrade_base_leaves_only_alembic_version` y `test_ca20_full_roundtrip_restores_the_expected_schema`: `f39f66eed513.downgrade()` hace `op.drop_table("cells")` sin eliminar antes la FK `fk_medications_cell_id_cells`, y PostgreSQL responde `DependentObjectsStillExistError: cannot drop table cells because other objects depend on it`.

## Bugfix registrado (2026-10-27)

Reporte del humano: `PUT /shelves/{id}` devolvia `IntegrityError: null value in column "location_id"` con traceback 500.

Causa raiz: `update_shelf` hacia `shelf.cells.clear()`, lo que eliminaba las celdas; SQLAlchemy intentaba anular la FK `medications.location_id`, que es NOT NULL.

Arreglos:
1. Sincronizacion de celdas por posicion en lugar de borrar y recrear (R-7, R-8).
2. Handler de `IntegrityError` y de excepcion no controlada para responder el envelope del proyecto (R-12).
3. `session.expire(shelf, ["cells"])` antes de releer, porque la respuesta devolvia celdas obsoletas del identity map.
4. Validacion de posiciones duplicadas en el schema (R-11).
5. `description` vacia normalizada a `NULL` (R-10).

## Rename `medications.location_id` → `cell_id` (2026-10-27)

Aprobado por el humano tras confirmar que la columna apuntaba a `cells.id` con el nombre heredado de la tabla eliminada.

- Migración `4ad3df9e109d_rename_medications_location_id_to_cell_id`: renombra la columna, el índice (`ix_medications_cell_id`) y la constraint (`fk_medications_cell_id_cells`), con `downgrade` simétrico.
- Modelo: `Medication.cell_id` + relación `Medication.cell`.
- **Cambio de contrato público** en `/api/v1/medications`: body `cell_id`, respuesta `cell_id`, filtro `?cell_id=`, `sort=cell_id`, y errores con `field: "cell_id"` (`cell_not_found` para inexistente; `422 validation_error` con `field: "cell_id"` para inactiva). El frontend debe actualizar esos puntos.

## Defectos encontrados por la revisión de la suite (corregidos)

1. `app/models/cell.py` no declaraba `Index("ix_cells_shelf_id")`, que la migración sí creaba: `alembic check` reportaba deriva. Corregido en el modelo.
2. `f39f66eed513.downgrade()` eliminaba `cells` sin soltar antes la FK de `medications`: `DependentObjectsStillExistError`. Corregido soltando `fk_medications_location_id_cells` y recreando la FK contra `locations`.

## Comportamiento pendiente de decisión (no modificado)

- `GET /shelves?search="   "` no recorta el término y devuelve 0 filas, mientras los demás catálogos lo tratan como ausencia de filtro.
- Un `sort` desconocido cae silenciosamente a `name` en lugar de responder `422`, a diferencia del resto de los listados que validan con `SORT_PATTERN`.

## Verificacion final

- `DATABASE_URL=...5434/dispensario_test uv run pytest -q` -> **688 passed**
- `uv run ruff check .` -> limpio
- `uv run ruff format --check .` -> 223 archivos formateados
- `uv run alembic check` (dev y test) -> sin operaciones nuevas
