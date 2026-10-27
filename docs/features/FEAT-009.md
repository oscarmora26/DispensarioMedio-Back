# FEAT-009 — Gestión de ubicaciones: CRUD, permisos y desactivación lógica

> **Nota de vigencia (2026-10-27).** Este documento describe el contrato tal como se aprobó en su fecha y se
> conserva como registro histórico. Desde **FEAT-017** la tabla `locations` fue reemplazada por
> `shelves` + `cells`: `/api/v1/locations` ya no existe (ahora son `/api/v1/shelves` y `/api/v1/cells`) y
> `medications.location_id` pasó a llamarse `medications.cell_id` (FK a `cells.id`). El contrato vigente
> está en [FEAT-017](FEAT-017.md).

## Contexto

La tabla `locations` ya existe (FEAT-003): `description CITEXT`, `shelf CITEXT`, `section CITEXT`, `cell CITEXT` con restricción única compuesta `uq_locations_position (shelf, section, cell)`, `is_active BOOLEAN DEFAULT true`, y relación `medications` (FK `medications.location_id → locations.id ON DELETE RESTRICT`). Hoy **no existe ninguna vía HTTP para crear, consultar, actualizar o desactivar ubicaciones**.

Sin esta feature, RF-06 (gestionar ubicaciones físicas) y US-17 (CRUD de ubicaciones, Sprint 3, 5 pts; estante, tramo, celda con combinación única) quedan sin implementar, y las futuras features de medicamentos (que exigen `location_id`) no tienen cómo poblar el catálogo por API. Es el tercer y último catálogo maestro (E2) y clona el patrón ya aprobado de `/drug-types` (FEAT-007) y `/brands` (FEAT-008), con la diferencia de que la unicidad es **compuesta** sobre la posición física en vez de sobre la descripción.

Fuente de verdad funcional: `DispensarioMedicoDocs/01-requerimientos-y-backlog.md` RF-06, RF-17, US-17, E2, §3 (matriz: Ubicaciones CRUD ADMIN / Consulta MEDICO) y §7 (CA-CRUD); `DispensarioMedicoDocs/03-api-rest.md` §2 (paginación, envelope, códigos), §4 (matriz `/locations`: CRUD ADMIN / GET MEDICO), §5 (endpoints), §6 (parámetros de listado) y §8 (`POST /locations {"description","shelf","section","cell","is_active"}`, combinación `(shelf, section, cell)` única); `DispensarioMedicoDocs/04-base-de-datos.md` §6.6 (tabla `locations`) y §6.7 (FK `medications.location_id`); `DispensarioMedicoDocs/07-manual-usuario.md` §4.4 (formulario: Descripción, Estante, Tramo, Celda; regla de combinación única).

## Alcance

- `app/schemas/location.py` (nuevo): `LocationCreate`, `LocationUpdate`, `LocationRead`, `LocationListResponse`.
- `app/repositories/location_repository.py` (nuevo): listado paginado con búsqueda/filtros/orden, `get_location_by_id`, detección de duplicados de posición compuesta case-insensitive (CITEXT).
- `app/services/location_service.py` (nuevo): `list_locations`, `get_location`, `create_location`, `update_location`, `deactivate_location`, traducción de `IntegrityError` (`uq_locations_position` → `409`).
- `app/api/v1/routes/locations.py` (nuevo) con tag `locations`, registrado en `app/api/v1/router.py`.
- Reutilización de: `app/api/deps.py` (`get_current_user`, `require_role`), `app/core/exceptions.py` (`AppError`, `NotFoundError`, `ConflictError`, envelope; `_validation_field` ya descarta prefijos `query`/`path`/`header`), `app/models/location.py`.
- Cinco endpoints descritos en "Contrato de API".
- Logs estructurados `location.created`, `location.updated`, `location.deactivated`.
- Documentación OpenAPI completa (tag, `summary`, `description`, `response_model`, códigos).
- Sin migraciones: `uv run alembic check` debe seguir limpio.

### Estructura de archivos resultante

```text
app/
├── api/v1/
│   ├── router.py                 # modificado: incluye locations
│   └── routes/locations.py       # nuevo
├── repositories/
│   └── location_repository.py    # nuevo
├── schemas/location.py           # nuevo
└── services/location_service.py  # nuevo
```

## Fuera de alcance

- CRUD de `/drug-types`, `/brands`, `/medications`, `/patients`, `/doctors`, `/visits`: esta feature solo deja `locations` consultable como prerrequisito de medicamentos.
- Unicidad sobre `description`: la descripción **no** es única; solo la posición `(shelf, section, cell)` lo es (regla 15, D-6).
- `GET /locations` filtrado por "solo activos" obligatorio: el filtro `is_active` es opcional; la restricción "no crear medicamentos con catálogos inactivos" pertenece a la feature de medicamentos (03-api-rest §8).
- Borrado físico de ubicaciones: la única baja es lógica (`is_active = false`). No hay `DELETE` SQL en ningún camino.
- Bloqueo de desactivar una ubicación con medicamentos asociados: la desactivación lógica **está permitida** aunque existan `medications` (el FK es `RESTRICT` solo contra borrado físico — ver D-7).
- Reactivación por endpoint dedicado: se reactiva con `PUT {"description", "shelf", "section", "cell", "is_active": true}` (reemplazo completo, D-1).
- Paginación por cursor, exportación, `audit_log` (RF-22 fuera de v1), refresh tokens, cambios de esquema, migraciones, `docker-compose.yml`, `Dockerfile`, `.env.example` o settings nuevas. `uv run alembic check` debe seguir limpio.
- CORS, frontend Angular (tabla, formulario, toasts), pruebas E2E.
- Ordenación por campos fuera del catálogo de D-3.

## Reglas de negocio

### Autorización y alcance

1. Lectura (`GET /locations`, `GET /locations/{id}`): `Authorization: Bearer <token>` y rol `ADMIN` **o** `MEDICO`, leído de la base (`user.role.name`) mediante `require_role("ADMIN", "MEDICO")`. El claim `role` del token no se usa (FEAT-002 regla 12).
2. Escritura (`POST`, `PUT`, `DELETE`): rol `ADMIN` exclusivamente, mediante `require_role("ADMIN")`. Un `MEDICO` autenticado recibe `403` `forbidden` en las tres, sin efectos en la base (CA-CRUD 8).
3. Sin cabecera, con esquema distinto de `Bearer`, con token inválido/expirado o de un usuario inexistente ⇒ `401` (`not_authenticated` o `invalid_token`, FEAT-002 reglas 11 y 12).
4. Un `ADMIN` o `MEDICO` desactivado pierde el acceso en la siguiente request (`403` `inactive_user`), incluso si el token sigue vigente (FEAT-002 regla 13).
5. Todos los errores usan el envelope global `{"detail": str, "code": str, "errors": [{"field","message"}]}`; `errors` es `[]` cuando no aplica y la clave `details` no existe (FEAT-002 D-1, FEAT-004 D-13). Códigos nuevos: `location_not_found` (404), `location_exists` (409). Se reutilizan `not_authenticated` / `invalid_token` (401), `inactive_user` / `forbidden` (403) y `validation_error` (422).

### Listado `GET /locations`

6. Parámetros: `page` (entero ≥ 1, por defecto `1`), `size` (entero 1..100, por defecto `20`), `search` (texto, máximo 100 caracteres), `is_active` (booleano opcional) y `sort` (catálogo cerrado de D-3, por defecto `description`).
7. `page`, `size` y `sort` se validan antes de consultar. `page < 1`, `size < 1`, `size > 100`, `sort` fuera del catálogo, `sort` vacío o `search` de más de 100 caracteres ⇒ `422` `validation_error` con `field` igual al nombre del parámetro (gracias a D-13 de FEAT-004, ya implementado).
8. `search` se recorta (`strip`). Vacío o solo espacios ⇒ **sin filtro**. Con contenido, búsqueda **case-insensitive y parcial** en los cuatro campos (`description`, `shelf`, `section`, `cell`, columnas `CITEXT`) con `ILIKE` y los caracteres `%`, `_` y `\` escapados literalmente, en `OR`: buscar `%` encuentra solo las ubicaciones con `%` en alguno de los cuatro campos, nunca todas.
9. `is_active` ausente ⇒ **sin filtro** (se devuelven activas e inactivas, CA-CRUD 1). `is_active=true` ⇒ solo activas; `is_active=false` ⇒ solo inactivas.
10. Orden: campo del catálogo de D-3, ascendente por defecto y descendente con prefijo `-`. Siempre se añade `locations.id ASC` como desempate estable, salvo cuando el campo ordenado ya es `id`. `created_at` usa `NULLS LAST` en ambos sentidos (nunca es `NULL` en la práctica; se conserva por coherencia con FEAT-004).
11. `total` es el número de filas que cumplen los filtros (sin `LIMIT`). `pages = ceil(total / size)` y exactamente `0` cuando `total == 0`. `page` se devuelve tal como se pidió aunque supere `pages` (en ese caso `items` es `[]`).
12. Cada elemento del listado y cada respuesta de ubicación tiene **exactamente** `id`, `description`, `shelf`, `section`, `cell`, `is_active` (D-4). Nunca se devuelven `created_at`/`updated_at` en la respuesta (CA-CRUD 4 exige **conservar** `created_at`, no exponerlo).

### Creación `POST /locations`

13. Body: `description`, `shelf`, `section`, `cell` obligatorios; `is_active` opcional (D-5, por defecto `true`). Ejemplo literal del §8: `{"description": "Estante A - Tramo 1 - Celda 2", "shelf": "A", "section": "1", "cell": "2", "is_active": true}`.
14. Los cuatro campos de texto: se recortan (`strip`); vacíos tras el recorte ⇒ `422` `validation_error` con `field` igual al nombre del campo. Longitud mínima 1 (tras `strip`) y máxima 100 caracteres cada uno (decisión humana confirmada). Capitalización **conservada**. Mensajes en español (CA-CRUD 9).
15. Duplicados: la posición `(shelf, section, cell)` ya existente, comparada **case-insensitive** por `CITEXT` en las tres columnas ⇒ `409` `location_exists` ("La ubicación ya existe"). Dos ubicaciones **pueden** compartir `description`; lo que no puede repetirse es la tripleta de posición. No se modifica la fila existente (no es *upsert*). La comparación ignora espacios laterales (los valores almacenados siempre van recortados, regla 14).
16. La ubicación se crea con `created_at`/`updated_at` por la base y el `is_active` recibido (por defecto `true`). Crear una ubicación inactiva está permitido.
17. Respuesta `201` con la forma de D-4. Si el `commit` viola `uq_locations_position` (carrera) ⇒ `409` `location_exists` (protección de carrera, D-8).

### Detalle `GET /locations/{location_id}`

18. `location_id` debe ser un entero; un valor no entero ⇒ `422` (validación de path). Un entero sin fila ⇒ `404` `location_not_found` ("La ubicación no existe").
19. Devuelve `200` con la forma de D-4, incluidas las ubicaciones inactivas (`is_active: false`). Accesible para `ADMIN` y `MEDICO`.

### Actualización `PUT /locations/{location_id}`

20. **Reemplazo completo** (D-1): el body exige `description`, `shelf`, `section`, `cell` e `is_active` (este último presente, puede ser `false`). Un body parcial ⇒ `422` con `field` del campo ausente.
21. Permite reactivar (`is_active: true`) y desactivar (`is_active: false`), con las mismas validaciones de texto que la creación (reglas 14–15). Los duplicados de posición se comprueban **excluyendo a la propia ubicación**: reenviar su misma posición (con otra capitalización o mismos espacios laterales) ⇒ `200`; la posición de otra ubicación ⇒ `409` `location_exists`. Cambiar solo `description` manteniendo la posición ⇒ `200`.
22. La actualización conserva `created_at` y refresca `updated_at` explícitamente con `clock_timestamp()` (D-9, patrón vigente de FEAT-004 D-14). Responde `200` con la forma de D-4.
23. Precedencia de errores: (a) `401`/`403` de autenticación y rol; (b) `422` de Pydantic (path, query y body); (c) `404` de la ubicación de la ruta; (d) `409` de unicidad; (e) escritura.
24. Solo `ADMIN`. Un `MEDICO` recibe `403` `forbidden` sin que la fila cambie.

### Desactivación `DELETE /locations/{location_id}`

25. Es una **desactivación lógica**: `is_active = false`, sin `DELETE` SQL (CA-CRUD 5, RF-17). La fila y sus referencias (`medications.location_id`, `RESTRICT`) se conservan.
26. `204 No Content` sin cuerpo y sin JSON (D-2). Starlette omite la cabecera `Content-Length` en las respuestas `204` (RFC 9110; FEAT-004 regla 31); no se añade manualmente.
27. **Idempotente**: si la ubicación ya está inactiva, vuelve a responder `204`, no escribe en la base (`updated_at` no cambia) y no emite un segundo `location.deactivated`.
28. `DELETE` de un `location_id` inexistente ⇒ `404` `location_not_found`. Solo `ADMIN`; un `MEDICO` recibe `403` `forbidden` y la fila no cambia.
29. Desactivar una ubicación con medicamentos asociados está **permitido** (D-7); la restricción "no operar con catálogos inactivos" se aplica en la feature de medicamentos, no aquí.

### Errores y códigos estables

30. Catálogo de códigos de la feature: `location_not_found` (404), `location_exists` (409). Reutilizados: `not_authenticated` / `invalid_token` (401), `inactive_user` / `forbidden` (403), `validation_error` (422).
31. Status HTTP de la feature: `200`, `201`, `204`, `401`, `403`, `404`, `409`, `422`. Un `500` solo puede provenir del fallback del framework.
32. Los mensajes de negocio van en español (CA-CRUD 9); los mensajes automáticos de Pydantic conservan su texto original en inglés (FEAT-002 regla 30). Los validadores propios de los cuatro campos usan mensajes en español.

### Registro (logs)

33. Eventos: `location.created`, `location.updated`, `location.deactivated`; cada uno con `location_id` (objetivo) y `actor_id` (usuario del token). `location.updated` no incluye valores de campos. `location.deactivated` se emite siempre que `is_active` pase de `true` a `false`, venga de `DELETE` o de `PUT`; el resto de actualizaciones emite `location.updated`.
34. Nunca se registran tokens, contraseñas ni datos de pacientes. Los cuatro campos son datos de catálogo (no PHI) pero no se incluyen en los logs por defecto para mantener el patrón mínimo de FEAT-004 D-9. El middleware sigue emitiendo `request.completed` con método, ruta, status, duración y `client_ip`.
35. Un `DELETE` idempotente sobre una ubicación ya inactiva no emite `location.deactivated` (no hubo cambio de estado).

## Supuestos

| ID | Supuesto | Justificación |
|---|---|---|
| **SUP-1** | Los únicos campos editables son `description`, `shelf`, `section`, `cell` e `is_active`; no hay código, pasillo, nivel ni orden manual. | El §8 de `03-api-rest.md` define `POST /locations` con exactamente esos cinco campos; ningún otro documento pide más. El formulario del §4.4 del manual pide los mismos cuatro (Descripción, Estante, Tramo, Celda). |
| **SUP-2** | La longitud 1..100 tras `strip` para los cuatro campos es decisión humana confirmada para esta feature (ver Historial de `feature-list.md` 2026-10-22), no requiere nueva pregunta de longitud. | El humano aprobó FEAT-009 con "longitudes 1..100 para los 4 campos (mismo patrón que FEAT-007/008)". Queda como supuesto registrado, no como pregunta abierta. |
| **SUP-3** | No se valida nada sobre `medications` al desactivar una ubicación; la integridad referencial la garantiza el FK `RESTRICT` contra borrado físico, que nunca se ejecuta. | `03-api-rest.md` §8 sitúa la regla "no crear con catálogos inactivos" en medicamentos, no en el catálogo. |
| **SUP-4** | La lectura es para ambos roles porque el médico necesita el catálogo para localizar medicamentos; la escritura es solo ADMIN según la matriz del §4. | Matriz de permisos de `01-requerimientos-y-backlog.md` §3 y `03-api-rest.md` §4 (`/locations`: CRUD ADMIN / GET MEDICO). |
| **SUP-5** | `description` puede repetirse entre ubicaciones distintas; la unicidad es solo de la tripleta de posición. | `04-base-de-datos.md` §6.6 fija la restricción única en `(shelf, section, cell)` y no declara `description` única (a diferencia de `brands`/`drug_types`). |

## Decisiones técnicas y desviaciones documentadas

| ID | Decisión | Justificación / impacto |
|---|---|---|
| **D-1** | `PUT /locations/{id}` es **reemplazo completo** de `description`, `shelf`, `section`, `cell` e `is_active` (los cinco obligatorios en el body). | Coherente con la semántica de `PUT` (RFC 9110) y con FEAT-004 D-1. Evita la ambigüedad de "campo ausente vs. valor actual" y hace el contrato verificable: body parcial ⇒ `422`. La reactivación es explícita con `is_active: true`. |
| **D-2** | `DELETE /locations/{id}` responde `204` sin cuerpo e idempotente si la ubicación ya está inactiva (no reescribe `updated_at` ni reemite el log). | Es el código del §2 para "operación exitosa sin contenido" y el patrón ya aprobado de FEAT-004 D-2 (la alternativa `200` del CA-CRUD genérico se descarta para mantener un único patrón de bajas lógicas en la API). La idempotencia evita `409`/`404` inútiles en reintentos del cliente. |
| **D-3** | Listado: `page=1`, `size=20` (1..100); `sort` ∈ `description`, `shelf`, `section`, `cell`, `created_at`, `is_active`, `id` con prefijo `-`; por defecto `description` ascendente; desempate `id ASC`; `created_at` con `NULLS LAST`; `search` case-insensitive sobre los cuatro campos en `OR` con `%`/`_`/`\` escapados; `is_active` sin filtro por defecto; `pages = ceil(total/size)` o `0`. | El §6 fija los parámetros y el máximo de 100, pero no los valores por defecto ni el catálogo de orden. Se elige un orden alfabético determinista (el uso natural de un catálogo) y estable (sin él, `OFFSET` puede repetir u omitir filas). El catálogo incluye las tres columnas de posición porque son el identificador físico natural. `sort` inválido ⇒ `422` en vez de ignorarse. |
| **D-4** | La respuesta de ubicación (`LocationRead`) tiene **exactamente** `id`, `description`, `shelf`, `section`, `cell`, `is_active`. | Es la forma literal del ejemplo del §8 (los cinco campos de entrada más el `id` generado). No se expone `created_at`/`updated_at` (el CA-CRUD 4 exige **conservar** `created_at`, no exponerlo). Misma forma única para listado, detalle, creación y actualización. |
| **D-5** | En `POST /locations`, `is_active` es opcional con valor por defecto `true`. | Es el ejemplo literal del §8 (`"is_active": true`) y coincide con el `server_default` de la base. Crear una ubicación inactiva está permitido (útil para precargar posiciones sin habilitarlas). |
| **D-6** | Los cuatro campos de texto: `strip`, no vacíos tras recortar, longitud 1..100 (decisión humana confirmada), capitalización **conservada**; la unicidad de posición es case-insensitive por `CITEXT` en las tres columnas (incluye capitalización y espacios laterales ya normalizados). `description` no participa en la unicidad. | `strip` + conservar capitalización replica el patrón de FEAT-004 D-12 y FEAT-007/008 D-6. El máximo de 100 por campo ya está confirmado por el humano (SUP-2). La unicidad compuesta case-insensitive viene del `UniqueConstraint("shelf","section","cell")` sobre `CITEXT` (FEAT-003). |
| **D-7** | Desactivar una ubicación con medicamentos asociados está permitido; nunca se emite `DELETE` físico, por lo que el FK `RESTRICT` no se viola. | RF-17 exige "activar y desactivar sin eliminar físicamente" (US-18: "Desactivar, no borrar"; `04-base-de-datos.md`: "No se borra si tiene medicamentos" se refiere al borrado físico). Prohibir la desactivación con referencias dejaría posiciones erróneas inamovibles; la protección real ("no crear medicamentos con catálogos inactivos") vive en la feature de medicamentos. |
| **D-8** | Los duplicados se apoyan en el índice único `uq_locations_position` y se traducen desde `IntegrityError` (`409 location_exists`); el pre-chequeo en el servicio solo mejora el mensaje. Sin `SELECT ... FOR UPDATE`: no hay regla de conteo global (a diferencia del "último ADMIN" de FEAT-004) que lo requiera. | La unicidad ya está garantizada por la base; el pre-chequeo evita el 90% de los 409 con buen mensaje y el `catch` cubre la carrera. No se introduce bloqueo porque no hay invariante multi-fila que proteger. |
| **D-9** | `updated_at` se refresca explícitamente con `func.clock_timestamp()` en `PUT` y en `DELETE` con cambio real. | Patrón vigente de FEAT-004 D-14: `onupdate=func.now()` es la hora de inicio de la transacción en PostgreSQL y no cambiaría dentro de una transacción larga de test. Se conserva la semántica sin tocar el esquema. `created_at` no se toca y ninguno de los dos se expone en `LocationRead`. |
| **D-10** | OpenAPI: tag `locations`, `summary`, `description`, `response_model` (salvo el `204`), y `responses` con `400`/`401`/`403`/`404`/`409`/`422` según el endpoint. Sin `MessageResponse`: el `DELETE` es `204` sin cuerpo. | §14 del contrato y RNF-09. La seguridad aparece automáticamente al depender de `require_role(...)` → `HTTPBearer`. |
| **D-11** | Logs con `location_id` y `actor_id` exclusivamente; sin valores de campos. `location.updated` no detalla el diff. | Coherente con FEAT-004 D-9 (mínimo necesario para rastrear quién operó sin escribir contenido de negocio en logs). `actor_id` permite trazabilidad sin `audit_log` (fuera de v1). |

## Modelo de datos

**No hay cambios de esquema ni migraciones nuevas.** `uv run alembic check` debe seguir imprimiendo `No new upgrade operations detected.` y `git status --porcelain alembic/` debe quedar vacío.

Columnas utilizadas (ya existentes, FEAT-003):

| Tabla | Columna | Uso en esta feature |
|---|---|---|
| `locations` | `id` (`BIGINT` identity) | Identificador de ruta y de respuesta. |
| `locations` | `description` (`CITEXT`) | Creación, actualización, búsqueda y orden. **No única.** |
| `locations` | `shelf` (`CITEXT`, parte de `uq_locations_position`) | Creación, actualización, búsqueda, orden y duplicados case-insensitive. |
| `locations` | `section` (`CITEXT`, parte de `uq_locations_position`) | Igual que `shelf`. |
| `locations` | `cell` (`CITEXT`, parte de `uq_locations_position`) | Igual que `shelf`. |
| `locations` | `is_active` (`BOOLEAN`, default `true`) | Desactivación lógica y filtro del listado. |
| `locations` | `created_at` / `updated_at` (`TIMESTAMPTZ`) | `created_at` se conserva; `updated_at` lo refresca el servicio con `clock_timestamp()` en cada `UPDATE` real (D-9). |
| `medications` | `location_id` (FK `RESTRICT`) | Solo lectura implícita: la desactivación lógica no toca esta tabla (D-7). |

Índices: `uq_locations_position` ya provee el índice único para la detección de duplicados y la protección de carrera. No se crea ningún índice nuevo: el catálogo es de volumen bajo (decenas de filas) e `ILIKE` con `LIMIT 100` no justifica un índice trigram en v1; si el volumen lo exige, se añade en una feature posterior sin cambiar el contrato.

Consultas previstas (`select()`, SQLAlchemy 2.x):

```python
# Listado (filtros opcionales) + conteo; search en OR sobre los 4 campos
pattern = f"%{escaped}%"
if search:
    search_filter = or_(
        Location.description.ilike(pattern, escape="\\"),
        Location.shelf.ilike(pattern, escape="\\"),
        Location.section.ilike(pattern, escape="\\"),
        Location.cell.ilike(pattern, escape="\\"),
    )
    conditions.append(search_filter)
if is_active is not None:
    conditions.append(Location.is_active.is_(is_active))
select(Location).where(*conditions).order_by(*order).limit(size).offset(offset)
select(func.count(Location.id)).where(*conditions)

# Duplicado de posición excluyendo a la propia ubicación
conditions = [
    Location.shelf == shelf,
    Location.section == section,
    Location.cell == cell,
]
if exclude_id is not None:
    conditions.append(Location.id != exclude_id)
select(Location.id).where(*conditions).limit(1)
```

### Contrato de repositorio (nombres que usa el servicio)

| Función | Firma resumida |
|---|---|
| `list_locations` | `(session, *, page, size, search, is_active, sort_field, sort_desc) -> tuple[list[Location], int]` |
| `find_location_by_position` | `(session, shelf, section, cell, *, exclude_id=None) -> Location \| None` |
| `get_location_by_id` | `(session, location_id) -> Location \| None` |

## Contrato de API

Prefijo `/api/v1`, `Content-Type: application/json`. Todos los errores usan el envelope de la regla 5: `{"detail", "code", "errors"}`.

### Autorización común

| Endpoint | Dependencia | Error del otro rol |
|---|---|---|
| `GET /locations`, `GET /locations/{id}` | `require_role("ADMIN", "MEDICO")` | — (ambos entran) |
| `POST`, `PUT`, `DELETE` | `require_role("ADMIN")` | `MEDICO` ⇒ `403` `forbidden`, sin escritura |

Errores comunes a los cinco endpoints:

| HTTP | `code` | Cuándo |
|---|---|---|
| `401` | `not_authenticated` | sin cabecera `Authorization` o esquema distinto de `Bearer` (`WWW-Authenticate: Bearer`) |
| `401` | `invalid_token` | firma inválida, token expirado, claims `sub`/`iat`/`exp` ausentes, `sub` no numérico o usuario inexistente |
| `403` | `inactive_user` | el usuario del token ya no está activo |
| `403` | `forbidden` | el usuario del token no tiene el rol exigido (p. ej. `MEDICO` escribiendo) |

### `GET /api/v1/locations`

- **Query params:**

| Parámetro | Tipo | Obligatorio | Restricciones |
|---|---|---|---|
| `page` | `int` | No | ≥ 1, por defecto `1` |
| `size` | `int` | No | 1..100, por defecto `20` |
| `search` | `str` | No | máximo 100 caracteres; `strip`; vacío ⇒ sin filtro; busca en `description`, `shelf`, `section` y `cell` (OR) |
| `is_active` | `bool` | No | sin filtro por defecto |
| `sort` | `str` | No | `description`, `shelf`, `section`, `cell`, `created_at`, `is_active`, `id` y sus formas con `-`; por defecto `description` |

- **Respuesta `200`** (`LocationListResponse`):

```json
{
  "items": [
    { "id": 1, "description": "Estante A - Tramo 1 - Celda 2", "shelf": "A", "section": "1", "cell": "2", "is_active": true }
  ],
  "total": 1,
  "page": 1,
  "size": 20,
  "pages": 1
}
```

- **Errores:** `422` `validation_error` (parámetros fuera de rango o `sort` inválido; `field` = nombre del parámetro) además de los comunes.

### `POST /api/v1/locations`

- **Body** (`LocationCreate`):

| Campo | Tipo | Obligatorio | Restricciones |
|---|---|---|---|
| `description` | `str` | Sí | `strip`, 1..100, capitalización conservada |
| `shelf` | `str` | Sí | `strip`, 1..100, capitalización conservada |
| `section` | `str` | Sí | `strip`, 1..100, capitalización conservada |
| `cell` | `str` | Sí | `strip`, 1..100, capitalización conservada |
| `is_active` | `bool` | No (default `true`) | — |

```json
{ "description": "Estante A - Tramo 1 - Celda 2", "shelf": "A", "section": "1", "cell": "2", "is_active": true }
```

- **Respuesta `201`** (`LocationRead`): `{ "id": 1, "description": "Estante A - Tramo 1 - Celda 2", "shelf": "A", "section": "1", "cell": "2", "is_active": true }`.
- **Errores:**

| HTTP | `code` | Cuándo |
|---|---|---|
| `403` | `forbidden` | el actor es `MEDICO` |
| `409` | `location_exists` | la posición `(shelf, section, cell)` ya existe (CITEXT, con `strip` aplicado) |
| `422` | `validation_error` | algún campo de texto ausente, vacío tras `strip` o de más de 100 caracteres |

Precedencia: `422` → `403` de rol (evaluado por la dependencia antes del servicio) → `409`.

### `GET /api/v1/locations/{location_id}`

- **Path param:** `location_id: int`.
- **Respuesta `200`** (`LocationRead`): misma forma que el `201`; incluye inactivas.
- **Errores:** `404` `location_not_found`, `422` si el id no es entero, además de los comunes (ambos roles).

### `PUT /api/v1/locations/{location_id}`

- **Path param:** `location_id: int`. Solo `ADMIN` (`MEDICO` ⇒ `403`).
- **Body** (`LocationUpdate`, reemplazo completo — D-1):

| Campo | Tipo | Obligatorio | Restricciones |
|---|---|---|---|
| `description` | `str` | **Sí** | igual que en `LocationCreate` |
| `shelf` | `str` | **Sí** | igual que en `LocationCreate` |
| `section` | `str` | **Sí** | igual que en `LocationCreate` |
| `cell` | `str` | **Sí** | igual que en `LocationCreate` |
| `is_active` | `bool` | **Sí** | — (permite reactivar con `true`) |

- **Respuesta `200`** (`LocationRead`) con el estado resultante.
- **Errores:**

| HTTP | `code` | Cuándo |
|---|---|---|
| `403` | `forbidden` | el actor es `MEDICO` |
| `404` | `location_not_found` | el id no existe |
| `409` | `location_exists` | la posición `(shelf, section, cell)` pertenece a otra ubicación |
| `422` | `validation_error` | body parcial, tipos, longitudes, path no entero |

### `DELETE /api/v1/locations/{location_id}`

- **Path param:** `location_id: int`. Solo `ADMIN` (`MEDICO` ⇒ `403`).
- **Respuesta `204 No Content`** sin cuerpo. Idempotente (D-2).
- **Errores:**

| HTTP | `code` | Cuándo |
|---|---|---|
| `403` | `forbidden` | el actor es `MEDICO` |
| `404` | `location_not_found` | el id no existe |
| `422` | `validation_error` | id no entero |

### OpenAPI

Cada endpoint declara `summary`, `description`, `response_model` (salvo el `204`), `responses` con los códigos de su tabla y el tag `locations`. El esquema `BearerAuth` ya existe (FEAT-002) y la seguridad es obligatoria en los cinco endpoints.

## Criterios de aceptación

Convención: `[test]` = aserción exacta que el reviewer implementa en `tests/**`; `[shell]` = comando con salida esperada. Los tests de integración reutilizan `db_session`, `db_conn`, `auth_client`/`auth_client_factory` y `auth_support` de FEAT-002 (ver "Plan de pruebas").

### Autorización y contrato

- [ ] **CA-1** `[test]` Con token de `MEDICO` activo: `GET /locations` y `GET /locations/{id}` ⇒ `200`; `POST`, `PUT`, `DELETE` ⇒ `403`, `code == "forbidden"`, `errors == []`, claves exactas `{detail, code, errors}` y la base no cambia (se comparan los cuatro campos/`is_active` del objetivo antes y después, y el conteo de filas).
- [ ] **CA-2** `[test]` Sin cabecera ⇒ `401` `not_authenticated` y `WWW-Authenticate` empieza por `Bearer`; con `Authorization: Basic abc` ⇒ igual; con token basura o firmado con otro secreto ⇒ `401` `invalid_token`; con token de un `ADMIN` o `MEDICO` desactivado ⇒ `403` `inactive_user` (sobre `GET /locations` y `POST /locations`).
- [ ] **CA-3** `[test]` `GET /locations/{id_inexistente}` con `ADMIN` ⇒ `404`, claves exactas `{detail, code, errors}`, `code == "location_not_found"`, `detail == "La ubicación no existe"`, `errors == []` y sin clave `details`.

### Listado

- [ ] **CA-4** `[test]` Sin parámetros: `page == 1`, `size == 20`, claves de nivel superior exactamente `{items, total, page, size, pages}`, `total == SELECT count(*) FROM locations` en la misma transacción, `len(items) == min(20, total)` y `pages == ceil(total / 20)`.
- [ ] **CA-5** `[test]` Límites: `size=1` y `size=100` ⇒ `200`; `size=0`, `size=101`, `size=-1`, `page=0`, `page=-1` ⇒ `422` con `field` igual a `"size"`/`"page"`; `page=9999` ⇒ `200`, `items == []`, `page == 9999`, `total` y `pages` sin cambios respecto a `page=1`.
- [ ] **CA-6** `[test]` Con ubicaciones de `shelf` con prefijo único: `sort=description` ordena ascendentemente; `sort=-description` descendentemente; `sort=shelf`, `sort=-shelf`, `sort=section`, `sort=-section`, `sort=cell`, `sort=-cell`, `sort=created_at`, `sort=-created_at`, `sort=is_active`, `sort=-is_active`, `sort=id`, `sort=-id` responden `200` y respetan el orden; `sort=shelf,shelf`, `sort=password_hash`, `sort=descriptionx`, `sort=` ⇒ `422` con `field == "sort"`; sin `sort` el orden es `description ASC`; el desempate `id ASC` se verifica ordenando por `created_at` con valores iguales.
- [ ] **CA-7** `[test]` `search` es case-insensitive y parcial sobre los cuatro campos: `search=<mitad de description en mayúsculas>` encuentra la ubicación; `search=<shelf exacto en minúsculas>` encuentra por estante; `search=<section>` encuentra por tramo; `search=<cell>` encuentra por celda; `search=%` devuelve solo ubicaciones con `%` literal en algún campo (se crean `Est %50` / `A%` y un control sin `%`); `search=_` solo los que contienen `_`; `search=\` no rompe y busca la barra literal; `search="   "` no aplica filtro (`total` igual al de sin `search`); `search` de 101 caracteres ⇒ `422` con `field == "search"`.
- [ ] **CA-8** `[test]` `is_active=true` devuelve solo activas, `is_active=false` solo inactivas y la ausencia del parámetro devuelve ambas; combinado con `search`, los filtros se aplican en conjunto.
- [ ] **CA-9** `[test]` `search` sin coincidencias ⇒ `items == []`, `total == 0`, `pages == 0` y `page`/`size` con los valores pedidos.
- [ ] **CA-10** `[test]` Cada `item` y cada respuesta de ubicación tiene exactamente `{id, description, shelf, section, cell, is_active}`; ninguna respuesta contiene `created_at`, `updated_at` ni claves ajenas.

### Creación

- [ ] **CA-11** `[test]` `POST /locations {"description": "Estante A - Tramo 1 - Celda 2", "shelf": "A", "section": "1", "cell": "2"}` (sin `is_active`) con `ADMIN` ⇒ `201`, cuerpo exacto de seis claves con `is_active is True`; en base: los cuatro campos recortados, `created_at`/`updated_at` no nulos. `POST` con `"is_active": false` ⇒ `201` con `is_active is False` y la fila nace inactiva (visible en `GET /{id}`).
- [ ] **CA-12** `[test]` Unicidad compuesta: existiendo `(shelf="A", section="1", cell="2")`, `POST` con `("a", "1", "2")` (otra capitalización en `shelf`) ⇒ `409` `location_exists`, `detail == "La ubicación ya existe"`; `POST` con `("  A  ", "1", "2")` (espacios laterales) ⇒ `409` igualmente; `POST` con la misma `description` pero posición `("B", "1", "2")` ⇒ `201` (la descripción repetida está permitida); `SELECT count(*) FROM locations WHERE shelf = 'A' AND section = '1' AND cell = '2'` es `1` tras los intentos duplicados.
- [ ] **CA-13** `[test]` Unicidad por columna individual: `POST` que difiere solo en `cell` (`("A","1","3")`), solo en `section` (`("A","2","2")`) o solo en `shelf` (`("B","1","2")`) ⇒ `201` en los tres casos; solo la tripleta idéntica (case-insensitive) colisiona.
- [ ] **CA-14** `[test]` `422` con `field` igual al nombre del campo: body `{}`, cada uno de los cuatro campos ausente por turno, cada campo `""`, cada campo `"   "` y cada campo de 101 caracteres. Valores de 1 y de 100 caracteres en los cuatro campos ⇒ `201`. `"  A  "` en `shelf` se almacena como `"A"` (recortado, capitalización conservada).
- [ ] **CA-15** `[test]` La respuesta del `201` no contiene `created_at`/`updated_at`; con `caplog` a `INFO`, la creación incluye `location.created` con `location_id` y `actor_id`.

### Detalle

- [ ] **CA-16** `[test]` `GET /locations/{id}` ⇒ `200` con los mismos valores que el `item` del listado; una ubicación inactiva devuelve `200` con `is_active is False`; un `id` no entero (`"abc"`) ⇒ `422` con `field == "location_id"`; accesible para `MEDICO` (200) sin diferencias respecto a `ADMIN`.

### Actualización

- [ ] **CA-17** `[test]` `PUT` completo (`{"description": "...", "shelf": "A", "section": "1", "cell": "9", "is_active": true}`) ⇒ `200` con el estado nuevo; en base cambian los campos enviados; `created_at` **no** cambia, `updated_at` sí (posterior al valor previo leído en la misma transacción).
- [ ] **CA-18** `[test]` `PUT` con la misma `description` y otra posición libre ⇒ `200`; `PUT {"description": <misma>, ..., "is_active": true}` sobre una ubicación inactiva ⇒ `200`, `is_active is True` (reactivación). `PUT` con `is_active: false` sobre una activa ⇒ `200`, `is_active is False`.
- [ ] **CA-19** `[test]` `PUT` con la posición de otra ubicación (otra capitalización incluida) ⇒ `409` `location_exists` y la fila no cambia; reenviando su propia posición (incluso con distinta capitalización o espacios laterales) ⇒ `200`; cambiando solo `description` ⇒ `200`.
- [ ] **CA-20** `[test]` `PUT` con `location_id` inexistente ⇒ `404` `location_not_found`; body sin `is_active` ⇒ `422` con `field == "is_active"`; body sin `shelf` ⇒ `422` con `field == "shelf"` (y análogamente `description`/`section`/`cell`); `PUT` de `MEDICO` ⇒ `403` y la fila no cambia.

### Desactivación

- [ ] **CA-21** `[test]` `DELETE /locations/{id}` de una ubicación activa con `ADMIN` ⇒ `204`, `response.content == b""`; la fila sigue existiendo con `is_active = false`; el log contiene `location.deactivated` con `location_id` y `actor_id`.
- [ ] **CA-22** `[test]` Segundo `DELETE` sobre la misma ubicación (idempotencia) ⇒ `204`, `is_active` sigue `false`, `updated_at` no cambia y no se emite un segundo `location.deactivated`; `DELETE` de un `id` inexistente ⇒ `404`; `DELETE` con `MEDICO` ⇒ `403` y la fila sigue activa.
- [ ] **CA-23** `[test]` Desactivar una ubicación referenciada por un `medication` (creado por SQL directo en la transacción) ⇒ `204` y `is_active = false`; la fila de `medications` sigue intacta (D-7).

### Integridad y errores

- [ ] **CA-24** `[test]` Un `IntegrityError` inyectado en el `commit` con el nombre `uq_locations_position` se traduce a `409` `location_exists`; no queda fila nueva y la sesión sigue usable.
- [ ] **CA-25** `[test]` Todos los errores de la feature respetan el envelope exacto `{detail, code, errors}` (sin `details`) y los códigos del catálogo de la regla 30 para cada escenario de las tablas de contrato (al menos un caso por código: `201`, `204`, `404`, `409`, `422`).
- [ ] **CA-26** `[test]` Con `caplog` a `INFO`, tras crear, actualizar y desactivar: aparecen `location.created`, `location.updated` y `location.deactivated`, cada uno con `location_id` y `actor_id`; `caplog.text` no contiene tokens ni contraseñas.

### OpenAPI y calidad

- [ ] **CA-27** `[shell]` OpenAPI:

  ```powershell
  uv run python -c "from app.main import app; o=app.openapi(); p=o['paths']; u=sorted(k for k in p if k.startswith('/api/v1/locations')); print(u); print(sorted({t for k in u for m in p[k].values() for t in m.get('tags', [])})); print(all(m.get('security') and m.get('summary') and m.get('description') for k in u for m in p[k].values())); print('content' in p['/api/v1/locations']['get']['responses']['200'], 'content' in p['/api/v1/locations']['post']['responses']['201']); print(sorted(p['/api/v1/locations']['post']['responses'])); print(sorted(p['/api/v1/locations/{location_id}']['delete']['responses']))"
  ```

  imprime:

  ```text
  ['/api/v1/locations', '/api/v1/locations/{location_id}']
  ['locations']
  True
  True True
  ['201', '401', '403', '409', '422']
  ['204', '401', '403', '404', '422']
  ```

  Además, `GET /api/v1/locations` declara `['200', '401', '403', '422']`, `GET /{id}` declara `['200', '401', '403', '404', '422']` y `PUT` declara `['200', '401', '403', '404', '409', '422']`.

- [ ] **CA-28** `[shell]` Sin cambios de esquema: `uv run alembic check` imprime `No new upgrade operations detected.` y `git status --porcelain alembic/` no imprime nada.
- [ ] **CA-29** `[shell]` `uv run ruff check .` sin errores y `uv run ruff format --check .` sin cambios pendientes.
- [ ] **CA-30** `[shell]` `uv run pytest -q` en verde, incluidos los tests de FEAT-002/003/004/007/008 (sin regresiones).

### Trazabilidad

| Requisito / fuente | Criterios |
|---|---|
| RF-06 / US-17 / E2 (CRUD de ubicaciones) | CA-4 a CA-23 |
| CA-CRUD 1–2 (listado paginado, `items/total/page/size/pages`) | CA-4, CA-5, CA-9 |
| CA-CRUD 3 (`201`, validación, `409` duplicados) | CA-11 a CA-15, CA-24 |
| CA-CRUD 4 (edición `200`, conserva `created_at`) | CA-17 |
| CA-CRUD 5 (baja lógica, sin borrado) | CA-21 a CA-23 |
| CA-CRUD 6 (`200`/`404` por ID) | CA-3, CA-16 |
| CA-CRUD 8 (escritura solo ADMIN, `403` a médico) | CA-1, CA-20, CA-22 |
| CA-CRUD 9 (validaciones y mensajes en español) | CA-5 a CA-7, CA-14 |
| CA-CRUD 10 (Swagger + pruebas) | CA-25 a CA-30 |
| §4 matriz (`/locations` CRUD ADMIN / GET MEDICO) | CA-1, CA-2, CA-16, CA-20, CA-22 |
| §8 (`POST` + combinación única `(shelf,section,cell)`) | CA-11 a CA-13, CA-19, CA-24 |
| §4.4 manual (formulario + regla de combinación) | CA-11, CA-12, CA-14 |
| RF-17 (activar/desactivar sin eliminar) | CA-18, CA-21 a CA-23 |

## Plan de pruebas

### Aislamiento

Se reutiliza la infraestructura de FEAT-002/FEAT-004 sin cambios:

1. `db_conn` (transacción revertida) y `db_session` (`join_transaction_mode="create_savepoint"`) de `tests/conftest.py`; ninguna escritura sobrevive al test.
2. `auth_client` / `auth_client_factory` sobrescriben `get_session` con `db_session`; limpiar en `finally` (ya lo hace la fixture).
3. Los usuarios se crean con `tests/auth_support.create_auth_user` y los tokens con `tests/auth_support.token_for(user_id, role=...)` + `auth_headers`. Se necesita un `ADMIN` actor y un `MEDICO` lector por test; no se depende de datos preexistentes (`total` se compara contra `SELECT count(*) FROM locations` en la transacción).
4. Las lecturas de verificación usan `db_conn` (misma conexión ⇒ ve los cambios no confirmados de `db_session`).
5. No se usan datos personales reales: posiciones sintéticas con `unique_suffix()` (p. ej. `shelf=f"A{unique_suffix()}"`); los casos con `%`, `_`, `\` usan sufijos fijos controlados.
6. El `medication` de CA-23 se inserta por SQL directo con `drug_type_id`/`brand_id` existentes o también insertados por SQL; todo dentro de la transacción revertida.
7. `get_login_rate_limiter()` no se toca en estos tests (ningún CA hace login salvo verificación opcional de que el actor sigue activo; máximo 1 login por test si se usa).

### Unitarias (sin base de datos)

| # | Test | CA |
|---|---|---|
| U-1 | `LocationCreate`: los cuatro campos obligatorios, `strip`, 1..100, default `is_active=True` | CA-14 |
| U-2 | `LocationUpdate`: los cinco campos obligatorios, `is_active` acepta `false`, textos con las mismas reglas | CA-20 |
| U-3 | `LocationRead`: serializa exactamente las seis claves | CA-10 |
| U-4 | Catálogo de `sort`: las 14 variantes válidas (7 campos × 2 sentidos) y el rechazo de inválidas (incluida la cadena vacía) | CA-6 |
| U-5 | Función de escape de `LIKE`: `%`, `_` y `\` se convierten en literales | CA-7 |
| U-6 | Cálculo de `pages`: `0` con `total=0`, techo con resto y exacto sin resto | CA-4, CA-9 |

### Integración (PostgreSQL + httpx)

| # | Test | CA |
|---|---|---|
| I-1 | `MEDICO` lee (`200`) y no escribe (`403` × 3 sin cambios en base) | CA-1 |
| I-2 | Sin token / esquema ajeno / token inválido / usuario inactivo | CA-2 |
| I-3 | `404` con envelope y sin `details` | CA-3 |
| I-4 | Defaults y fórmula de paginación | CA-4 |
| I-5 | Límites de `page`/`size` y página fuera de rango | CA-5 |
| I-6 | Los 7 campos de `sort` en ambos sentidos, `NULLS LAST`, desempate e inválidos | CA-6 |
| I-7 | `search` en los 4 campos, case-insensitive, parcial, escapado de comodines y longitud | CA-7 |
| I-8 | Filtro `is_active` y combinación con `search` | CA-8 |
| I-9 | Sin resultados: `pages == 0` | CA-9 |
| I-10 | Forma exacta de `item` en las cuatro respuestas con ubicación | CA-10, CA-16 |
| I-11 | Creación feliz activa/inactiva | CA-11 |
| I-12 | Duplicados compuestos `CITEXT` (capitalización, espacios, descripción repetida permitida) | CA-12 |
| I-13 | Unicidad por columna individual (difieren en una sola columna ⇒ `201`) | CA-13 |
| I-14 | Validaciones de `LocationCreate` y normalización `strip` en los 4 campos | CA-14 |
| I-15 | Log de creación con `location_id`/`actor_id` | CA-15 |
| I-16 | Detalle activo/inactivo, `422` de path y lectura `MEDICO` | CA-16 |
| I-17 | `PUT` feliz con conservación de `created_at` y refresco de `updated_at` | CA-17 |
| I-18 | `PUT` con cambio de posición libre, reactiva y desactiva | CA-18 |
| I-19 | `PUT` duplicados ajenos vs. propios (incluido cambio solo de `description`) | CA-19 |
| I-20 | `PUT` errores de body, `404` y `403` de `MEDICO` | CA-20 |
| I-21 | `DELETE` feliz, idempotente, `404` y `403` de `MEDICO` | CA-21, CA-22 |
| I-22 | `DELETE` de ubicación con medicamentos asociados | CA-23 |
| I-23 | Envelope uniforme por cada código del catálogo | CA-25 |

### Integridad

| # | Test | CA |
|---|---|---|
| C-1 | Traducción de `IntegrityError` por nombre de restricción (`uq_locations_position`) | CA-24 |

### Logs, OpenAPI y calidad

| # | Test | CA |
|---|---|---|
| S-1 | Los tres eventos con `location_id`/`actor_id` y sin secretos | CA-15, CA-26 |
| O-1 | OpenAPI: paths, tag, seguridad, `summary`/`description` y códigos | CA-27 |
| Q-1 | `alembic check` y `git status --porcelain alembic/` | CA-28 |
| Q-2 | `ruff check` y `ruff format --check` | CA-29 |
| Q-3 | `pytest -q` completo | CA-30 |

## Riesgos y dependencias

| ID | Riesgo | Impacto | Mitigación / decisión |
|---|---|---|---|
| **R-1** | La unicidad es compuesta, no por `description`: un implementer que clone FEAT-008 sin adaptar podría imponer `description` única o buscar duplicados solo por descripción. | Alto | Reglas 15 y 21, D-6/SUP-5, CA-12/CA-13/CA-19; el repositorio expone `find_location_by_position`, no `find_by_description`. |
| **R-2** | `PUT` es reemplazo completo de cinco campos: un cliente que envíe solo `description` recibe `422`; no hay bloqueo optimista (last write wins). | Bajo | D-1; el frontend hace `GET` antes de `PUT`. El volumen y la concurrencia de edición de catálogos son mínimos. |
| **R-3** | Desactivar una ubicación no impide su uso hasta que la feature de medicamentos aplique "no crear con catálogos inactivos"; temporalmente se puede prescribir con una ubicación inactiva. | Medio | D-7; la regla del §8 se implementa en US-20. El listado permite filtrar `is_active=true` desde el primer día. |
| **R-4** | `ILIKE` en `OR` sobre cuatro columnas con comodines escapados: un escape mal construido convertiría `%` en comodín o rompería con `\`. | Bajo | Escapado explícito de `\`, `%` y `_` con `escape="\\"` aplicado una vez al término y reutilizado en las cuatro condiciones; CA-7 lo verifica por campo y con los tres caracteres especiales. |
| **R-5** | Sin índice trigram, `search ILIKE '%...%'` en cuatro columnas es secuencial; irrelevante con decenas de filas pero costoso si el catálogo creciera a miles. | Bajo | Sin índice nuevo en v1 (sección "Modelo de datos"); si el volumen lo exige, índice trigram en feature posterior sin cambio de contrato. |
| **R-6** | `04-base-de-datos.md` §6.5 fija `description` 2–100 para `brands`, pero nada fija mínimos para `locations`; la decisión humana 1..100 para los 4 campos ya cierra el tema, pero diverge del "2–100" documental. | Bajo | SUP-2: la decisión humana prevalece y está registrada; no se abre pregunta de longitud. |
| **R-7** | `sort` con 7 campos duplica combinaciones y el `OR` del `search` puede devolver la misma fila por varios campos; el conteo debe usar `count(id)` sobre las mismas condiciones, no `count(DISTINCT)` innecesario. | Bajo | Modelo de datos: conteo y listado comparten `conditions`; CA-4/CA-7 verifican `total` contra SQL directo. |

**Dependencias**

- **FEAT-001 (`done`)**: FastAPI, sesión async, logging con redacción, envelope y middlewares.
- **FEAT-003 (`done`)**: tabla `locations` (`CITEXT` × 4, `uq_locations_position`, `is_active` default `true`), FK `medications.location_id` (`RESTRICT`).
- **FEAT-002 (`done`)**: `get_current_user`, `require_role` (incluye `require_role("ADMIN", "MEDICO")` — ambos roles están en `ALLOWED_ROLES`), envelope `{detail, code, errors}`.
- **FEAT-004 (`done`, patrón)**: paginación (`page/size/search/is_active/sort`), `_validation_field` (D-13), `204` idempotente (D-2), `clock_timestamp()` (D-14), estructura routes → services → repositories.
- **FEAT-007/FEAT-008 (`done`, plantilla directa)**: el implementer clona `brands` renombrando a `location_*` (`location_not_found` / `location_exists`, eventos `location.*`, sort default `description`), y sustituye la unicidad simple por la compuesta de posición.
- **Bloquea**: la feature de medicamentos (US-20) necesita este CRUD para poblar `location_id`.
- **No modifica** `app/core/config.py`, `app/api/deps.py`, `app/core/security.py`, `app/models/`, `alembic/` ni dependencias de `pyproject.toml`.

**Datos sensibles**

- Los cuatro campos son datos de catálogo físico (p. ej. "A", "1", "2"), no datos de paciente; aun así no se registran en logs (D-11).
- Los tests usan posiciones sintéticas dentro de una transacción revertida; no se usan datos personales reales.
- `audit_log` no se escribe en esta feature (RF-22 fuera de v1); el rastro queda en los eventos estructurados.

## Preguntas abiertas

1. **¿Debe el `search` ponderar u ordenar por campo coincidente** (p. ej. priorizar coincidencia en `shelf` sobre `description`), o basta el `OR` plano con el orden de D-3? La spec propone `OR` plano. **No bloquea**: el comportamiento propuesto es implementable y verificable (CA-7).
2. **¿Debe `description` autogenerarse a partir de la posición** (p. ej. `"Estante A - Tramo 1 - Celda 2"`) cuando se envía vacía, o seguir siendo obligatoria? La spec la mantiene obligatoria según el ejemplo del §8 y el formulario del manual. **No bloquea**: el contrato propuesto es implementable y verificable (CA-14).

No hay más bloqueos: permisos (matriz §3/§4), forma del `POST` (§8), paginación (§6), unicidad compuesta case-insensitive (`uq_locations_position` en FEAT-003), longitudes 1..100 (decisión humana 2026-10-22) y baja lógica (RF-17/CA-CRUD 5) están definidos en las fuentes.

## Historial

- 2026-10-22 — creada por spec-author.
- 2026-10-22 — `proposed` → `approved`: aprobación explícita del humano ("Sí, aprobar e iniciar" vía pregunta de aprobación). Decisiones: longitudes 1..100 para los 4 campos (mismo patrón que FEAT-007/008).
