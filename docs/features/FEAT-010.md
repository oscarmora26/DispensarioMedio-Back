# FEAT-010 — Gestión de medicamentos: CRUD, permisos y desactivación lógica

> **Nota de vigencia (2026-10-27).** Este documento describe el contrato tal como se aprobó en su fecha y se
> conserva como registro histórico. Desde **FEAT-017** la tabla `locations` fue reemplazada por
> `shelves` + `cells`: `/api/v1/locations` ya no existe (ahora son `/api/v1/shelves` y `/api/v1/cells`) y
> `medications.location_id` pasó a llamarse `medications.cell_id` (FK a `cells.id`). El contrato vigente
> está en [FEAT-017](FEAT-017.md).

**Estado:** `approved` (aprobada explícitamente por el humano el 2026-10-22)
**Fecha:** 2026-10-22
**Alcance:** `GET/POST /api/v1/medications`, `GET/PUT/DELETE /api/v1/medications/{medication_id}`, con lectura para `ADMIN` y `MEDICO` y escritura solo para `ADMIN`, reutilizando autenticación JWT, envelope de error `{detail, code, errors}` y desactivación lógica de FEAT-002/FEAT-004 y el patrón de catálogos de FEAT-007/008/009.
**Fuente de verdad funcional:** `DispensarioMedicoDocs/01-requerimientos-y-backlog.md` RF-07, RF-17, US-20/US-21/US-22, E3, §3 (matriz: Medicamentos CRUD ADMIN / Consulta MEDICO) y §7 (CA-CRUD); `DispensarioMedicoDocs/03-api-rest.md` §2 (paginación, envelope, códigos), §4 (matriz `/medications`: CRUD ADMIN / GET MEDICO), §5 (endpoints), §6 (parámetros de listado) y §8 (`POST /medications {description, drug_type_id, brand_id, location_id, dose, is_active}` + reglas: descripciones únicas donde aplique, única `(description, brand_id, dose)`, no crear con catálogos inactivos).

## Contexto

La tabla `medications` ya existe (FEAT-003): `description CITEXT`, `drug_type_id`/`brand_id`/`location_id` FK (`RESTRICT`), `dose CITEXT`, `is_active BOOLEAN DEFAULT true`, restricción única `uq_medications_identity (description, brand_id, dose)` e índice trigram `ix_medications_description_trgm`. Hoy **no existe ninguna vía HTTP para crear, consultar, actualizar o desactivar medicamentos**.

Sin esta feature, RF-07 (gestionar medicamentos asociando tipo, marca y ubicación) y US-20/US-21/US-22 (CRUD, consulta con ubicación física e integridad, Sprint 4) quedan sin implementar. Es la primera feature que combina los tres catálogos maestros (E2: FEAT-007/008/009) y la que aplica la regla del §8 "no crear con catálogos inactivos".

## Alcance

- `app/schemas/medication.py` (nuevo): `MedicationCreate`, `MedicationUpdate`, `MedicationRead`, `MedicationListResponse`.
- `app/repositories/medication_repository.py` (nuevo): listado paginado con búsqueda/filtros/FK/orden, `get_medication_by_id`, detección de duplicados de identidad `(description, brand_id, dose)` case-insensitive (CITEXT).
- `app/services/medication_service.py` (nuevo): `list_medications`, `get_medication`, `create_medication`, `update_medication`, `deactivate_medication`, validación de FK (existen + activos), traducción de `IntegrityError` (`uq_medications_identity` → `409`).
- `app/api/v1/routes/medications.py` (nuevo) con tag `medications`, registrado en `app/api/v1/router.py`.
- Reutilización de: `app/api/deps.py` (`get_current_user`, `require_role`), `app/core/exceptions.py` (`AppError`, `NotFoundError`, `ConflictError`, envelope; `_validation_field` ya descarta prefijos `query`/`path`/`header`), `app/models/medication.py`, y lecturas de `drug_types`/`brands`/`locations` para validar FK (repositorios de FEAT-007/008/009).
- Cinco endpoints descritos en "Contrato de API".
- Logs estructurados `medication.created`, `medication.updated`, `medication.deactivated`.
- Documentación OpenAPI completa (tag, `summary`, `description`, `response_model`, códigos).
- Sin migraciones: `uv run alembic check` debe seguir limpio.

### Estructura de archivos resultante

```text
app/
├── api/v1/
│   ├── router.py                 # modificado: incluye medications
│   └── routes/medications.py     # nuevo
├── repositories/
│   └── medication_repository.py  # nuevo
├── schemas/medication.py         # nuevo
└── services/medication_service.py# nuevo
```

## Fuera de alcance

- CRUD de `/drug-types`, `/brands`, `/locations`, `/patients`, `/doctors`, `/visits`: esta feature solo **consume** los tres catálogos como FK prerrequisito (US-20).
- Respuesta anidada con los objetos de catálogo: `MedicationRead` lleva solo los FK planos (D-4); exponer `drug_type`/`brand`/`location` anidados queda para una feature posterior si US-21 lo exige.
- Control de inventario, stock o existencias (excluido de v1, §2 de requerimientos).
- Borrado físico de medicamentos: la única baja es lógica (`is_active = false`). No hay `DELETE` SQL en ningún camino.
- Bloqueo de desactivar un medicamento con visitas asociadas: la desactivación lógica **está permitida** aunque existan `visit_medications` (D-7); la protección real es "no registrar visitas con medicamentos inactivos", que pertenece a la feature de visitas (US-30/US-22).
- Reactivación por endpoint dedicado: se reactiva con `PUT` de reemplazo completo con `"is_active": true` (D-1).
- Paginación por cursor, exportación, `audit_log` (RF-22 fuera de v1), refresh tokens, cambios de esquema, migraciones, `docker-compose.yml`, `Dockerfile`, `.env.example` o settings nuevas. `uv run alembic check` debe seguir limpio.
- CORS, frontend Angular (tabla, formulario, toasts), pruebas E2E.
- Ordenación por campos fuera del catálogo de D-3.

## Reglas de negocio

### Autorización y alcance

1. Lectura (`GET /medications`, `GET /medications/{id}`): `Authorization: Bearer <token>` y rol `ADMIN` **o** `MEDICO`, leído de la base (`user.role.name`) mediante `require_role("ADMIN", "MEDICO")`. El claim `role` del token no se usa (FEAT-002 regla 12).
2. Escritura (`POST`, `PUT`, `DELETE`): rol `ADMIN` exclusivamente, mediante `require_role("ADMIN")`. Un `MEDICO` autenticado recibe `403` `forbidden` en las tres, sin efectos en la base (CA-CRUD 8).
3. Sin cabecera, con esquema distinto de `Bearer`, con token inválido/expirado o de un usuario inexistente ⇒ `401` (`not_authenticated` o `invalid_token`, FEAT-002 reglas 11 y 12).
4. Un `ADMIN` o `MEDICO` desactivado pierde el acceso en la siguiente request (`403` `inactive_user`), incluso si el token sigue vigente (FEAT-002 regla 13).
5. Todos los errores usan el envelope global `{"detail": str, "code": str, "errors": [{"field","message"}]}`; `errors` es `[]` cuando no aplica y la clave `details` no existe (FEAT-002 D-1, FEAT-004 D-13). Códigos nuevos: `medication_not_found` (404), `medication_exists` (409). Reutilizados para FK: `drug_type_not_found` / `brand_not_found` / `location_not_found` (404). Reutilizados: `not_authenticated` / `invalid_token` (401), `inactive_user` / `forbidden` (403) y `validation_error` (422).

### Listado `GET /medications`

6. Parámetros: `page` (entero ≥ 1, por defecto `1`), `size` (entero 1..100, por defecto `20`), `search` (texto, máximo 100 caracteres), `is_active` (booleano opcional), `drug_type_id` / `brand_id` / `location_id` (enteros ≥ 1 opcionales) y `sort` (catálogo cerrado de D-3, por defecto `description`).
7. `page`, `size`, `sort` y los tres filtros FK se validan antes de consultar. `page < 1`, `size < 1`, `size > 100`, `sort` fuera del catálogo, `sort` vacío, `search` de más de 100 caracteres o un FK ≤ 0 / no entero ⇒ `422` `validation_error` con `field` igual al nombre del parámetro (gracias a D-13 de FEAT-004, ya implementado).
8. `search` se recorta (`strip`). Vacío o solo espacios ⇒ **sin filtro**. Con contenido, búsqueda **case-insensitive y parcial** en `medications.description` **o** `medications.dose` (columnas `CITEXT`, condición `OR`) con `ILIKE` y los caracteres `%`, `_` y `\` escapados literalmente: buscar `%` encuentra solo los medicamentos cuya descripción o dosis contiene un `%`, nunca todos.
9. `is_active` ausente ⇒ **sin filtro** (se devuelven activos e inactivos, CA-CRUD 1). `is_active=true` ⇒ solo activos; `is_active=false` ⇒ solo inactivos. Cada FK ausente ⇒ sin filtro por ese catálogo; presente ⇒ igualdad exacta (`medications.drug_type_id == X`). Los filtros se combinan en `AND` con `search`.
10. Orden: campo del catálogo de D-3, ascendente por defecto y descendente con prefijo `-`. Siempre se añade `medications.id ASC` como desempate estable, salvo cuando el campo ordenado ya es `id`. `created_at` usa `NULLS LAST` en ambos sentidos (nunca es `NULL` en la práctica; se conserva por coherencia con FEAT-004).
11. `total` es el número de filas que cumplen los filtros (sin `LIMIT`). `pages = ceil(total / size)` y exactamente `0` cuando `total == 0`. `page` se devuelve tal como se pidió aunque supere `pages` (en ese caso `items` es `[]`).
12. Cada elemento del listado y cada respuesta de medicamento tiene **exactamente** `id`, `description`, `drug_type_id`, `brand_id`, `location_id`, `dose`, `is_active` (D-4). Nunca se devuelven `created_at`/`updated_at` ni objetos de catálogo anidados (CA-CRUD 4 exige **conservar** `created_at`, no exponerlo).

### Creación `POST /medications`

13. Body: `description`, `drug_type_id`, `brand_id`, `location_id`, `dose` obligatorios; `is_active` opcional (D-5, por defecto `true`). Ejemplo literal del §8: `{"description": "Acetaminofén", "drug_type_id": 1, "brand_id": 3, "location_id": 7, "dose": "500 mg", "is_active": true}`.
14. `description` y `dose`: se recortan (`strip`); vacías tras el recorte ⇒ `422` `validation_error` con `field` igual al nombre del campo. Longitud mínima 1 (tras `strip`) y máxima 100 caracteres cada una (D-6). Capitalización **conservada**. Mensajes en español (CA-CRUD 9). Los FK son enteros ≥ 1; un FK no entero o < 1 ⇒ `422` con `field` igual a su nombre.
15. **FK existen**: cada FK debe apuntar a una fila existente de su catálogo. Un FK sin fila ⇒ `404` con el código del catálogo (`drug_type_not_found` / `brand_not_found` / `location_not_found`) y mensaje en español ("El tipo de fármaco no existe" / "La marca no existe" / "La ubicación no existe"). Cuando faltan varios FK, se reporta el primero en el orden `drug_type_id`, `brand_id`, `location_id`.
16. **FK activos** (§8 "No se puede crear con catálogos inactivos"): cada FK debe apuntar a una fila con `is_active = true`. Un FK existente pero inactivo ⇒ `422` `validation_error` con `errors == [{"field": "<fk>", "message": "<mensaje en español>"}]` (`"El tipo de fármaco está inactivo"` / `"La marca está inactiva"` / `"La ubicación está inactiva"`). Cuando hay varios FK inactivos, se reporta el primero en el mismo orden de la regla 15. Precedencia FK: inexistente (404) antes que inactivo (422); ver regla 23.
17. Duplicados: la tripleta `(description, brand_id, dose)` ya existente, con `description` y `dose` comparadas **case-insensitive** por `CITEXT` (espacios laterales ya normalizados por la regla 14) y `brand_id` por igualdad exacta ⇒ `409` `medication_exists` ("El medicamento ya existe"). La misma `description`+`dose` con otro `brand_id` ⇒ `201`; la misma `description`+`brand_id` con otra `dose` ⇒ `201`. No se modifica la fila existente (no es *upsert*).
18. El medicamento se crea con `created_at`/`updated_at` por la base y el `is_active` recibido (por defecto `true`). Crear un medicamento inactivo está permitido (sus FK deben existir y estar activos igualmente). Respuesta `201` con la forma de D-4. Si el `commit` viola `uq_medications_identity` (carrera) ⇒ `409` `medication_exists` (protección de carrera, D-8).

### Detalle `GET /medications/{medication_id}`

19. `medication_id` debe ser un entero; un valor no entero ⇒ `422` (validación de path). Un entero sin fila ⇒ `404` `medication_not_found` ("El medicamento no existe").
20. Devuelve `200` con la forma de D-4 (FK planos, sin anidación), incluidos los medicamentos inactivos (`is_active: false`). Accesible para `ADMIN` y `MEDICO`.

### Actualización `PUT /medications/{medication_id}`

21. **Reemplazo completo** (D-1): el body exige los seis campos `description`, `drug_type_id`, `brand_id`, `location_id`, `dose` e `is_active` (este último presente, puede ser `false`). Un body parcial ⇒ `422` con `field` del campo ausente.
22. Permite reactivar (`is_active: true`) y desactivar (`is_active: false`), permite cambiar los FK (con las validaciones de las reglas 14–16 aplicadas a los valores nuevos) y aplica las mismas validaciones de `description`/`dose` y unicidad que la creación (reglas 14 y 17). Los duplicados se comprueban **excluyendo al propio medicamento**: reenviar su misma identidad (con otra capitalización o mismos espacios laterales) ⇒ `200`; la identidad de otro medicamento ⇒ `409` `medication_exists`.
23. Precedencia de errores: (a) `401`/`403` de autenticación y rol; (b) `422` de Pydantic (path, query y body, incluidos FK no enteros o < 1); (c) `404` del medicamento de la ruta; (d) `404` del FK inexistente (orden `drug_type_id`, `brand_id`, `location_id`); (e) `422` del FK inactivo (mismo orden); (f) `409` de unicidad; (g) escritura.
24. Solo `ADMIN`. Un `MEDICO` recibe `403` `forbidden` sin que la fila cambie. La actualización conserva `created_at` y refresca `updated_at` explícitamente con `clock_timestamp()` (D-9). Responde `200` con la forma de D-4.
25. `PUT` que solo cambia `is_active` (mismos `description`/`dose`/FK) nunca colisiona con la unicidad (es su propia identidad).

### Desactivación `DELETE /medications/{medication_id}`

26. Es una **desactivación lógica**: `is_active = false`, sin `DELETE` SQL (CA-CRUD 5, RF-17). La fila y sus referencias (`visit_medications.medication_id`) se conservan.
27. `204 No Content` sin cuerpo y sin JSON (D-2). Starlette omite la cabecera `Content-Length` en las respuestas `204` (RFC 9110; FEAT-004 regla 31); no se añade manualmente.
28. **Idempotente**: si el medicamento ya está inactivo, vuelve a responder `204`, no escribe en la base (`updated_at` no cambia) y no emite un segundo `medication.deactivated`.
29. `DELETE` de un `medication_id` inexistente ⇒ `404` `medication_not_found`. Solo `ADMIN`; un `MEDICO` recibe `403` `forbidden` y la fila no cambia.
30. Desactivar un medicamento referenciado por visitas (`visit_medications`) está **permitido** (D-7); la restricción "paciente y medicamentos deben estar activos" (US-30) se aplica al registrar la visita, no aquí.

### Errores y códigos estables

31. Catálogo de códigos de la feature: `medication_not_found` (404), `medication_exists` (409). Reutilizados para FK: `drug_type_not_found` / `brand_not_found` / `location_not_found` (404). Reutilizados: `not_authenticated` / `invalid_token` (401), `inactive_user` / `forbidden` (403), `validation_error` (422, incluye FK inactivo con `field` igual al FK).
32. Status HTTP de la feature: `200`, `201`, `204`, `401`, `403`, `404`, `409`, `422`. Un `500` solo puede provenir del fallback del framework. Los mensajes de negocio van en español (CA-CRUD 9); los mensajes automáticos de Pydantic conservan su texto original en inglés (FEAT-002 regla 30). Los validadores propios de `description`/`dose` y los mensajes de FK usan español.

### Registro (logs)

33. Eventos: `medication.created`, `medication.updated`, `medication.deactivated`; cada uno con `medication_id` (objetivo) y `actor_id` (usuario del token). `medication.updated` no incluye valores de campos. `medication.deactivated` se emite siempre que `is_active` pase de `true` a `false`, venga de `DELETE` o de `PUT`; el resto de actualizaciones emite `medication.updated`.
34. Nunca se registran tokens, contraseñas ni datos de pacientes. `description`/`dose` son datos de catálogo (no PHI) pero no se incluyen en los logs por defecto para mantener el patrón mínimo de FEAT-004 D-9. El middleware sigue emitiendo `request.completed` con método, ruta, status, duración y `client_ip`.
35. Un `DELETE` idempotente sobre un medicamento ya inactivo no emite `medication.deactivated` (no hubo cambio de estado).

## Supuestos

| ID | Supuesto | Justificación |
|---|---|---|
| **SUP-1** | Los únicos campos editables son `description`, `drug_type_id`, `brand_id`, `location_id`, `dose` e `is_active`; no hay código, precio, stock ni orden manual. | El §8 de `03-api-rest.md` define `POST /medications` con exactamente esos seis campos; el control de inventario/stock está excluido de v1 (§2 de requerimientos). |
| **SUP-2** | La longitud máxima de 100 para `description` y `dose` es propuesta de esta spec (D-6), no regla de negocio externa; si el humano fija otro valor, se ajusta el `Field` y CA-13 sin romper el contrato. | Ninguna fuente fija min/max (el §8 solo muestra `"Acetaminofén"` y `"500 mg"`); el límite evita `CITEXT` sin cota en el contrato HTTP y coincide con el máximo de `search` y con el patrón 1..100 confirmado por el humano para los catálogos (FEAT-009). Ver preguntas abiertas 1–2. |
| **SUP-3** | No se valida nada sobre `visit_medications` al desactivar un medicamento; la integridad referencial la garantiza el FK `RESTRICT` contra borrado físico, que nunca se ejecuta. | `03-api-rest.md` §11 sitúa la regla "paciente y medicamentos deben estar activos" en el registro de visitas (US-30), no en el catálogo de medicamentos; análogo a FEAT-007/008/009 D-7. |
| **SUP-4** | La lectura es para ambos roles porque el médico necesita consultar medicamentos, dosis y ubicación para prescribir (US-21/US-30); la escritura es solo ADMIN según la matriz del §4. | Matriz de permisos de `01-requerimientos-y-backlog.md` §3 y `03-api-rest.md` §4 (`/medications`: CRUD ADMIN / GET MEDICO). |

## Decisiones técnicas y desviaciones documentadas

| ID | Decisión | Justificación / impacto |
|---|---|---|
| **D-1** | `PUT /medications/{id}` es **reemplazo completo** de los seis campos (los cinco de negocio + `is_active`, todos obligatorios en el body). | Coherente con la semántica de `PUT` (RFC 9110) y con FEAT-004 D-1 / FEAT-007/008/009 D-1. Evita la ambigüedad de "campo ausente vs. valor actual" y hace el contrato verificable: body parcial ⇒ `422`. La reactivación es explícita con `is_active: true`. |
| **D-2** | `DELETE /medications/{id}` responde `204` sin cuerpo e idempotente si el medicamento ya está inactivo (no reescribe `updated_at` ni reemite el log). | Es el código del §2 para "operación exitosa sin contenido" y el patrón ya aprobado de FEAT-004 D-2. La idempotencia evita `409`/`404` inútiles en reintentos del cliente. |
| **D-3** | Listado: `page=1`, `size=20` (1..100); `sort` ∈ `description`, `dose`, `drug_type_id`, `brand_id`, `location_id`, `created_at`, `is_active`, `id` con prefijo `-`; por defecto `description` ascendente; desempate `id ASC`; `created_at` con `NULLS LAST`; `search` case-insensitive con `OR` sobre `description`+`dose` con `%`/`_`/`\` escapados; `is_active` y los tres FK sin filtro por defecto; `pages = ceil(total/size)` o `0`. | El §6 fija los parámetros y el máximo de 100, pero no los valores por defecto ni el catálogo de orden. Se elige un orden alfabético determinista (el uso natural del catálogo) y estable (sin él, `OFFSET` puede repetir u omitir filas). Los FK entran al `sort` porque son el identificador operativo del medicamento (útil para agrupar por marca/ubicación). `sort` inválido ⇒ `422` en vez de ignorarse. |
| **D-4** | La respuesta de medicamento (`MedicationRead`) tiene **exactamente** `id`, `description`, `drug_type_id`, `brand_id`, `location_id`, `dose`, `is_active`: FK **planos, sin objetos anidados**. | Es la forma del ejemplo del §8 (los seis campos de entrada más el `id` generado) y replica el patrón "Read = Create + id" de FEAT-007/008/009. Las relaciones del modelo usan `lazy="raise"`, por lo que anidar obligaría a cargas extra (riesgo de N+1 en el listado); el frontend compone la vista de US-21 ("Acetaminofén 500 mg — Estante A…") uniendo con los endpoints ya existentes de los tres catálogos. No se expone `created_at`/`updated_at` (el CA-CRUD 4 exige **conservar** `created_at`, no exponerlo). Misma forma única para listado, detalle, creación y actualización. |
| **D-5** | En `POST /medications`, `is_active` es opcional con valor por defecto `true`. | Es el ejemplo literal del §8 (`"is_active": true`) y coincide con el `server_default` de la base. Crear un medicamento inactivo está permitido (útil para precargar sin habilitar); sus FK deben existir y estar activos igualmente (reglas 15–16). |
| **D-6** | `description` y `dose`: `strip`, no vacías tras recortar, longitud 1..100, capitalización **conservada**; la unicidad es case-insensitive por `CITEXT` en ambas columnas (incluye capitalización y espacios laterales ya normalizados); `brand_id` compara por igualdad exacta. | `strip` + conservar capitalización replica el patrón de FEAT-004 D-12 y FEAT-007/008/009 D-6. El máximo de 100 es propuesta explícita (SUP-2, preguntas 1–2). La unicidad compuesta case-insensitive viene del `UniqueConstraint("description","brand_id","dose")` sobre `CITEXT` (FEAT-003). |
| **D-7** | Desactivar un medicamento con visitas asociadas (`visit_medications`) está permitido; nunca se emite `DELETE` físico, por lo que el FK `RESTRICT` no se viola. | RF-17 exige "activar y desactivar sin eliminar físicamente" (US-18: "Desactivar, no borrar"). Prohibir la desactivación con referencias dejaría medicamentos erróneos inamovibles; la protección real ("medicamentos deben estar activos" al registrar visitas, §11) vive en la feature de visitas (US-30/US-22). |
| **D-8** | Los duplicados se apoyan en el índice único `uq_medications_identity` y se traducen desde `IntegrityError` (`409 medication_exists`); el pre-chequeo en el servicio solo mejora el mensaje. Sin `SELECT ... FOR UPDATE`: no hay regla de conteo global (a diferencia del "último ADMIN" de FEAT-004) que lo requiera. | La unicidad ya está garantizada por la base; el pre-chequeo evita el 90% de los 409 con buen mensaje y el `catch` cubre la carrera. No se introduce bloqueo porque no hay invariante multi-fila que proteger. |
| **D-9** | `updated_at` se refresca explícitamente con `func.clock_timestamp()` en `PUT` y en `DELETE` con cambio real. | Patrón vigente de FEAT-004 D-14: `onupdate=func.now()` es la hora de inicio de la transacción en PostgreSQL y no cambiaría dentro de una transacción larga de test. Se conserva la semántica sin tocar el esquema. `created_at` no se toca y ninguno de los dos se expone en `MedicationRead`. |
| **D-10** | Validación de FK en dos niveles: inexistente ⇒ `404` con el código propio del catálogo (`drug_type_not_found` / `brand_not_found` / `location_not_found`); existente pero inactivo ⇒ `422` `validation_error` con `field` igual al FK; precedencia de la regla 23 (ruta → FK inexistente → FK inactivo → unicidad). | El `404` reutiliza el vocabulario ya aprobado de FEAT-007/008/009 (el cliente ya sabe interpretarlo) y distingue "apunta a nada" de "apunta a algo deshabilitado". El `422` con `field` mantiene el manejo uniforme de validaciones a nivel de campo (CA-CRUD 9) y encaja en el §2 (`422` = validación) sin inventar un código `400` nuevo. La precedencia hace el comportamiento determinista y testeable cuando fallan varios FK a la vez. |
| **D-11** | OpenAPI: tag `medications`, `summary`, `description`, `response_model` (salvo el `204`), y `responses` con los códigos de su tabla según el endpoint. Sin `MessageResponse`: el `DELETE` es `204` sin cuerpo. Logs con `medication_id` y `actor_id` exclusivamente; sin `description`/`dose` ni valores de campos. | §14 del contrato y RNF-09. La seguridad aparece automáticamente al depender de `require_role(...)` → `HTTPBearer`. Los logs mínimos replican FEAT-004 D-9 (trazabilidad sin `audit_log`, fuera de v1, y sin escribir contenido de negocio en logs). |

## Modelo de datos

**No hay cambios de esquema ni migraciones nuevas.** `uv run alembic check` debe seguir imprimiendo `No new upgrade operations detected.` y `git status --porcelain alembic/` debe quedar vacío.

Columnas utilizadas (ya existentes, FEAT-003):

| Tabla | Columna | Uso en esta feature |
|---|---|---|
| `medications` | `id` (`BIGINT` identity) | Identificador de ruta y de respuesta. |
| `medications` | `description` (`CITEXT`, parte de `uq_medications_identity`) | Creación, actualización, búsqueda, orden y duplicados case-insensitive. |
| `medications` | `drug_type_id` (FK `drug_types.id` `RESTRICT`) | Validación existe+activo (reglas 15–16), filtros y orden del listado. |
| `medications` | `brand_id` (FK `brands.id` `RESTRICT`, parte de `uq_medications_identity`) | Igual que `drug_type_id` + parte de la identidad única. |
| `medications` | `location_id` (FK `locations.id` `RESTRICT`) | Igual que `drug_type_id`. |
| `medications` | `dose` (`CITEXT`, parte de `uq_medications_identity`) | Creación, actualización, búsqueda (`OR` con `description`), orden y duplicados case-insensitive. |
| `medications` | `is_active` (`BOOLEAN`, default `true`) | Desactivación lógica y filtro del listado. |
| `medications` | `created_at` / `updated_at` (`TIMESTAMPTZ`) | `created_at` se conserva; `updated_at` lo refresca el servicio con `clock_timestamp()` en cada `UPDATE` real (D-9). |
| `visit_medications` | `medication_id` (FK `RESTRICT`) | Solo lectura implícita: la desactivación lógica no toca esta tabla (D-7). |

Índices: `uq_medications_identity` ya provee el índice único para la detección de duplicados y la protección de carrera; `ix_medications_{drug_type,brand,location}_id` ya aceleran los filtros FK; `ix_medications_description_trgm` ya acelera el `ILIKE '%...%'` sobre `description`. No se crea ningún índice nuevo en v1; si el volumen lo exige, se añade un trigram sobre `dose` en una feature posterior sin cambiar el contrato.

Consultas previstas (`select()`, SQLAlchemy 2.x):

```python
# Listado (filtros opcionales) + conteo; search en OR sobre description y dose
pattern = f"%{escaped}%"
if search:
    search_filter = or_(
        Medication.description.ilike(pattern, escape="\\"),
        Medication.dose.ilike(pattern, escape="\\"),
    )
    conditions.append(search_filter)
if is_active is not None:
    conditions.append(Medication.is_active.is_(is_active))
if drug_type_id is not None:
    conditions.append(Medication.drug_type_id == drug_type_id)
if brand_id is not None:
    conditions.append(Medication.brand_id == brand_id)
if location_id is not None:
    conditions.append(Medication.location_id == location_id)
select(Medication).where(*conditions).order_by(*order).limit(size).offset(offset)
select(func.count(Medication.id)).where(*conditions)

# Duplicado de identidad excluyendo al propio medicamento
conditions = [
    Medication.description == description,
    Medication.brand_id == brand_id,
    Medication.dose == dose,
]
if exclude_id is not None:
    conditions.append(Medication.id != exclude_id)
select(Medication.id).where(*conditions).limit(1)
```

### Contrato de repositorio (nombres que usa el servicio)

| Función | Firma resumida |
|---|---|
| `list_medications` | `(session, *, page, size, search, is_active, drug_type_id, brand_id, location_id, sort_field, sort_desc) -> tuple[list[Medication], int]` |
| `find_medication_by_identity` | `(session, description, brand_id, dose, *, exclude_id=None) -> Medication \| None` |
| `get_medication_by_id` | `(session, medication_id) -> Medication \| None` |

La validación de FK reutiliza `get_drug_type_by_id` / `get_brand_by_id` / `get_location_by_id` de los repositorios de FEAT-007/008/009 (existen + `is_active`).

## Contrato de API

Prefijo `/api/v1`, `Content-Type: application/json`. Todos los errores usan el envelope de la regla 5: `{"detail", "code", "errors"}`.

### Autorización común

| Endpoint | Dependencia | Error del otro rol |
|---|---|---|
| `GET /medications`, `GET /medications/{id}` | `require_role("ADMIN", "MEDICO")` | — (ambos entran) |
| `POST`, `PUT`, `DELETE` | `require_role("ADMIN")` | `MEDICO` ⇒ `403` `forbidden`, sin escritura |

Errores comunes a los cinco endpoints:

| HTTP | `code` | Cuándo |
|---|---|---|
| `401` | `not_authenticated` | sin cabecera `Authorization` o esquema distinto de `Bearer` (`WWW-Authenticate: Bearer`) |
| `401` | `invalid_token` | firma inválida, token expirado, claims `sub`/`iat`/`exp` ausentes, `sub` no numérico o usuario inexistente |
| `403` | `inactive_user` | el usuario del token ya no está activo |
| `403` | `forbidden` | el usuario del token no tiene el rol exigido (p. ej. `MEDICO` escribiendo) |

### `GET /api/v1/medications`

- **Query params:**

| Parámetro | Tipo | Obligatorio | Restricciones |
|---|---|---|---|
| `page` | `int` | No | ≥ 1, por defecto `1` |
| `size` | `int` | No | 1..100, por defecto `20` |
| `search` | `str` | No | máximo 100 caracteres; `strip`; vacío ⇒ sin filtro; busca en `description` **o** `dose` |
| `is_active` | `bool` | No | sin filtro por defecto |
| `drug_type_id` | `int` | No | ≥ 1; sin filtro por defecto |
| `brand_id` | `int` | No | ≥ 1; sin filtro por defecto |
| `location_id` | `int` | No | ≥ 1; sin filtro por defecto |
| `sort` | `str` | No | `description`, `dose`, `drug_type_id`, `brand_id`, `location_id`, `created_at`, `is_active`, `id` y sus formas con `-`; por defecto `description` |

- **Respuesta `200`** (`MedicationListResponse`):

```json
{
  "items": [
    { "id": 8, "description": "Acetaminofén", "drug_type_id": 1, "brand_id": 3, "location_id": 7, "dose": "500 mg", "is_active": true }
  ],
  "total": 1,
  "page": 1,
  "size": 20,
  "pages": 1
}
```

- **Errores:** `422` `validation_error` (parámetros fuera de rango, FK ≤ 0 o `sort` inválido; `field` = nombre del parámetro) además de los comunes.

### `POST /api/v1/medications`

- **Body** (`MedicationCreate`):

| Campo | Tipo | Obligatorio | Restricciones |
|---|---|---|---|
| `description` | `str` | Sí | `strip`, 1..100 (D-6), capitalización conservada |
| `drug_type_id` | `int` | Sí | ≥ 1; debe existir y estar activo (reglas 15–16) |
| `brand_id` | `int` | Sí | ≥ 1; debe existir y estar activo (reglas 15–16) |
| `location_id` | `int` | Sí | ≥ 1; debe existir y estar activo (reglas 15–16) |
| `dose` | `str` | Sí | `strip`, 1..100 (D-6), capitalización conservada |
| `is_active` | `bool` | No (default `true`) | — |

```json
{ "description": "Acetaminofén", "drug_type_id": 1, "brand_id": 3, "location_id": 7, "dose": "500 mg", "is_active": true }
```

- **Respuesta `201`** (`MedicationRead`): `{ "id": 8, "description": "Acetaminofén", "drug_type_id": 1, "brand_id": 3, "location_id": 7, "dose": "500 mg", "is_active": true }`.
- **Errores:**

| HTTP | `code` | Cuándo |
|---|---|---|
| `403` | `forbidden` | el actor es `MEDICO` |
| `404` | `drug_type_not_found` / `brand_not_found` / `location_not_found` | el FK no tiene fila (primero en orden `drug_type_id`, `brand_id`, `location_id`) |
| `409` | `medication_exists` | la identidad `(description, brand_id, dose)` ya existe (CITEXT) |
| `422` | `validation_error` | `description`/`dose` ausentes, vacías tras `strip` o de más de 100 caracteres; FK no entero o < 1; FK existente pero inactivo (`field` = el FK) |

Precedencia: `422` Pydantic → `403` de rol (evaluado por la dependencia antes del servicio) → `404` FK → `422` FK inactivo → `409`.

### `GET /api/v1/medications/{medication_id}`

- **Path param:** `medication_id: int`.
- **Respuesta `200`** (`MedicationRead`): misma forma que el `201`; incluye inactivos; sin objetos anidados.
- **Errores:** `404` `medication_not_found`, `422` si el id no es entero, además de los comunes (ambos roles).

### `PUT /api/v1/medications/{medication_id}`

- **Path param:** `medication_id: int`. Solo `ADMIN` (`MEDICO` ⇒ `403`).
- **Body** (`MedicationUpdate`, reemplazo completo — D-1):

| Campo | Tipo | Obligatorio | Restricciones |
|---|---|---|---|
| `description` | `str` | **Sí** | igual que en `MedicationCreate` |
| `drug_type_id` | `int` | **Sí** | ≥ 1; debe existir y estar activo |
| `brand_id` | `int` | **Sí** | ≥ 1; debe existir y estar activo |
| `location_id` | `int` | **Sí** | ≥ 1; debe existir y estar activo |
| `dose` | `str` | **Sí** | igual que en `MedicationCreate` |
| `is_active` | `bool` | **Sí** | — (permite reactivar con `true`) |

- **Respuesta `200`** (`MedicationRead`) con el estado resultante.
- **Errores:**

| HTTP | `code` | Cuándo |
|---|---|---|
| `403` | `forbidden` | el actor es `MEDICO` |
| `404` | `medication_not_found` | el id de la ruta no existe |
| `404` | `drug_type_not_found` / `brand_not_found` / `location_not_found` | un FK del body no tiene fila |
| `409` | `medication_exists` | la identidad `(description, brand_id, dose)` pertenece a otro medicamento |
| `422` | `validation_error` | body parcial, tipos, longitudes, FK < 1, FK inactivo, path no entero |

Precedencia: regla 23.

### `DELETE /api/v1/medications/{medication_id}`

- **Path param:** `medication_id: int`. Solo `ADMIN` (`MEDICO` ⇒ `403`).
- **Respuesta `204 No Content`** sin cuerpo. Idempotente (D-2).
- **Errores:**

| HTTP | `code` | Cuándo |
|---|---|---|
| `403` | `forbidden` | el actor es `MEDICO` |
| `404` | `medication_not_found` | el id no existe |
| `422` | `validation_error` | id no entero |

### OpenAPI

Cada endpoint declara `summary`, `description`, `response_model` (salvo el `204`), `responses` con los códigos de su tabla y el tag `medications`. El esquema `BearerAuth` ya existe (FEAT-002) y la seguridad es obligatoria en los cinco endpoints.

## Criterios de aceptación

Convención: `[test]` = aserción exacta que el reviewer implementa en `tests/**`; `[shell]` = comando con salida esperada. Los tests de integración reutilizan `db_session`, `db_conn`, `auth_client`/`auth_client_factory` y `auth_support` de FEAT-002 (ver "Plan de pruebas"). Los catálogos prerrequisito se crean por API (`POST /drug-types`, `/brands`, `/locations` con `ADMIN`) o por SQL directo dentro de la transacción revertida.

### Autorización y contrato

- [ ] **CA-1** `[test]` Con token de `MEDICO` activo: `GET /medications` y `GET /medications/{id}` ⇒ `200`; `POST`, `PUT`, `DELETE` ⇒ `403`, `code == "forbidden"`, `errors == []`, claves exactas `{detail, code, errors}` y la base no cambia (se comparan los siete campos del objetivo antes y después, y el conteo de filas).
- [ ] **CA-2** `[test]` Sin cabecera ⇒ `401` `not_authenticated` y `WWW-Authenticate` empieza por `Bearer`; con `Authorization: Basic abc` ⇒ igual; con token basura o firmado con otro secreto ⇒ `401` `invalid_token`; con token de un `ADMIN` o `MEDICO` desactivado ⇒ `403` `inactive_user` (sobre `GET /medications` y `POST /medications`).
- [ ] **CA-3** `[test]` `GET /medications/{id_inexistente}` con `ADMIN` ⇒ `404`, claves exactas `{detail, code, errors}`, `code == "medication_not_found"`, `detail == "El medicamento no existe"`, `errors == []` y sin clave `details`.

### Listado

- [ ] **CA-4** `[test]` Sin parámetros: `page == 1`, `size == 20`, claves de nivel superior exactamente `{items, total, page, size, pages}`, `total == SELECT count(*) FROM medications` en la misma transacción, `len(items) == min(20, total)` y `pages == ceil(total / 20)`.
- [ ] **CA-5** `[test]` Límites: `size=1` y `size=100` ⇒ `200`; `size=0`, `size=101`, `size=-1`, `page=0`, `page=-1` ⇒ `422` con `field` igual a `"size"`/`"page"`; `page=9999` ⇒ `200`, `items == []`, `page == 9999`, `total` y `pages` sin cambios respecto a `page=1`.
- [ ] **CA-6** `[test]` Con medicamentos de `description` con prefijo único: `sort=description` ordena ascendentemente; `sort=-description` descendentemente; `sort=dose`, `sort=-dose`, `sort=drug_type_id`, `sort=-drug_type_id`, `sort=brand_id`, `sort=-brand_id`, `sort=location_id`, `sort=-location_id`, `sort=created_at`, `sort=-created_at`, `sort=is_active`, `sort=-is_active`, `sort=id`, `sort=-id` responden `200` y respetan el orden; `sort=dose,dose`, `sort=password_hash`, `sort=descriptionx`, `sort=` ⇒ `422` con `field == "sort"`; sin `sort` el orden es `description ASC`; el desempate `id ASC` se verifica ordenando por `created_at` con valores iguales.
- [ ] **CA-7** `[test]` `search` es case-insensitive y parcial sobre `description` **o** `dose`: `search=<mitad de description en mayúsculas>` encuentra el medicamento; `search=<dosis exacta en minúsculas>` (p. ej. `"500 mg"`) encuentra por dosis aunque la descripción no coincida; `search=%` devuelve solo medicamentos con `%` literal en `description` o `dose` (se crean `Jarabe %50` / dosis `% x` y un control sin `%`); `search=_` solo los que contienen `_`; `search=\` no rompe y busca la barra literal; `search="   "` no aplica filtro (`total` igual al de sin `search`); `search` de 101 caracteres ⇒ `422` con `field == "search"`.
- [ ] **CA-8** `[test]` `is_active=true` devuelve solo activos, `is_active=false` solo inactivos y la ausencia del parámetro devuelve ambos; `drug_type_id=X`, `brand_id=Y`, `location_id=Z` filtran por igualdad exacta cada uno; combinados entre sí y con `search`, los filtros se aplican en conjunto (`AND`); un FK ≤ 0 o no entero en query ⇒ `422` con `field` igual a su nombre.
- [ ] **CA-9** `[test]` `search` sin coincidencias ⇒ `items == []`, `total == 0`, `pages == 0` y `page`/`size` con los valores pedidos.
- [ ] **CA-10** `[test]` Cada `item` y cada respuesta de medicamento tiene exactamente `{id, description, drug_type_id, brand_id, location_id, dose, is_active}` (7 claves, FK planos); ninguna respuesta contiene `created_at`, `updated_at`, `drug_type`, `brand`, `location` ni claves ajenas.

### Creación

- [ ] **CA-11** `[test]` `POST /medications` con el body del §8 (sin `is_active`) con `ADMIN` ⇒ `201`, cuerpo exacto de siete claves con `is_active is True`; en base: `description`/`dose` recortados, `created_at`/`updated_at` no nulos. `POST` con `"is_active": false` ⇒ `201` con `is_active is False` y la fila nace inactiva (visible en `GET /{id}`, regla D-5).
- [ ] **CA-12** `[test]` Unicidad compuesta: existiendo `("Acetaminofén", brand=1, "500 mg")`, `POST ("ACETAMINOFÉN", brand=1, "500 MG")` ⇒ `409` `medication_exists`, `detail == "El medicamento ya existe"`, y el conteo de la identidad sigue en `1`; `POST ("  Acetaminofén  ", brand=1, "500 mg")` ⇒ `409` igualmente; `POST` con la misma `description`+`dose` pero otro `brand_id` ⇒ `201`; `POST` con la misma `description`+`brand_id` pero otra `dose` (`"750 mg"`) ⇒ `201`.
- [ ] **CA-13** `[test]` `422` con `field` igual al nombre del campo: body `{}`, cada uno de `description`/`dose`/FK ausente por turno, `description`/`dose` en `""` y en `"   "`, cada una de 101 caracteres, cada FK en `0`, `-1` y `"abc"`. Valores de 1 y de 100 caracteres en `description` y `dose` ⇒ `201`. `"  500 mg  "` se almacena como `"500 mg"` (recortado, capitalización conservada).
- [ ] **CA-14** `[test]` FK inexistente: `POST` con `drug_type_id` sin fila ⇒ `404` `drug_type_not_found` (`detail == "El tipo de fármaco no existe"`); con `brand_id` sin fila ⇒ `404` `brand_not_found`; con `location_id` sin fila ⇒ `404` `location_not_found`; con los tres inexistentes ⇒ `404` `drug_type_not_found` (orden de la regla 15). No queda fila nueva en ninguno de los casos.
- [ ] **CA-15** `[test]` FK inactivo: con `drug_type` inactivo (desactivado por `DELETE /drug-types/{id}`) ⇒ `422` `validation_error`, `errors == [{"field": "drug_type_id", "message": "El tipo de fármaco está inactivo"}]`; análogo con `brand` inactiva (`field == "brand_id"`, `"La marca está inactiva"`) y `location` inactiva (`field == "location_id"`, `"La ubicación está inactiva"`); con `drug_type` inexistente **y** `brand` inactiva ⇒ `404` (precedencia inexistente → inactivo, regla 23). Tampoco se crea la fila cuando el propio `POST` pide `"is_active": false`.
- [ ] **CA-16** `[test]` La respuesta del `201` no contiene `created_at`/`updated_at` ni objetos anidados; con `caplog` a `INFO`, la creación incluye `medication.created` con `medication_id` y `actor_id`.

### Detalle

- [ ] **CA-17** `[test]` `GET /medications/{id}` ⇒ `200` con los mismos valores que el `item` del listado; un medicamento inactivo devuelve `200` con `is_active is False`; un `id` no entero (`"abc"`) ⇒ `422` con `field == "medication_id"`; accesible para `MEDICO` (200) sin diferencias respecto a `ADMIN`.

### Actualización

- [ ] **CA-18** `[test]` `PUT` completo (seis campos, p. ej. cambio de `dose` a `"750 mg"` y de `location_id`) ⇒ `200` con el estado nuevo; en base cambian los campos enviados; `created_at` **no** cambia, `updated_at` sí (posterior al valor previo leído en la misma transacción).
- [ ] **CA-19** `[test]` `PUT` con la misma identidad y `"is_active": true` sobre un medicamento inactivo ⇒ `200`, `is_active is True` (reactivación); con `"is_active": false` sobre uno activo ⇒ `200`, `is_active is False`. `PUT` que cambia los FK a otros catálogos activos ⇒ `200`; a un FK inexistente ⇒ `404` con el código del catálogo; a un FK inactivo ⇒ `422` con `field` igual al FK (reglas 15–16 aplicadas al `PUT`).
- [ ] **CA-20** `[test]` `PUT` con la identidad de otro medicamento (otra capitalización en `description`/`dose` incluida) ⇒ `409` `medication_exists` y la fila no cambia; reenviando su propia identidad (incluso con distinta capitalización o espacios laterales) ⇒ `200`; `PUT` que solo cambia `is_active` ⇒ `200` (regla 25).
- [ ] **CA-21** `[test]` `PUT` con `medication_id` inexistente ⇒ `404` `medication_not_found` (aunque los FK del body también sean malos: precedencia (c) antes que (d)/(e)); body sin `is_active` ⇒ `422` con `field == "is_active"`; body sin `dose` ⇒ `422` con `field == "dose"` (y análogamente `description`/FK — reemplazo completo, D-1); `PUT` de `MEDICO` ⇒ `403` y la fila no cambia.

### Desactivación

- [ ] **CA-22** `[test]` `DELETE /medications/{id}` de un medicamento activo con `ADMIN` ⇒ `204`, `response.content == b""`; la fila sigue existiendo con `is_active = false`; el log contiene `medication.deactivated` con `medication_id` y `actor_id`.
- [ ] **CA-23** `[test]` Segundo `DELETE` sobre el mismo medicamento (idempotencia) ⇒ `204`, `is_active` sigue `false`, `updated_at` no cambia y no se emite un segundo `medication.deactivated`; `DELETE` de un `id` inexistente ⇒ `404`; `DELETE` con `MEDICO` ⇒ `403` y la fila sigue activa.
- [ ] **CA-24** `[test]` Desactivar un medicamento referenciado por una `visit_medication` (creada por SQL directo en la transacción) ⇒ `204` y `is_active = false`; la fila de `visit_medications` sigue intacta (D-7).

### Integridad y errores

- [ ] **CA-25** `[test]` Un `IntegrityError` inyectado en el `commit` con el nombre `uq_medications_identity` se traduce a `409` `medication_exists`; no queda fila nueva y la sesión sigue usable.
- [ ] **CA-26** `[test]` Todos los errores de la feature respetan el envelope exacto `{detail, code, errors}` (sin `details`) y los códigos del catálogo de la regla 31 para cada escenario de las tablas de contrato (al menos un caso por código: `medication_not_found`, `medication_exists`, `drug_type_not_found`/`brand_not_found`/`location_not_found`, `validation_error` de FK inactivo, `forbidden`, `not_authenticated`).
- [ ] **CA-27** `[test]` Con `caplog` a `INFO`, tras crear, actualizar y desactivar: aparecen `medication.created`, `medication.updated` y `medication.deactivated`, cada uno con `medication_id` y `actor_id`; `caplog.text` no contiene tokens, contraseñas ni datos de pacientes.

### OpenAPI y calidad

- [ ] **CA-28** `[shell]` OpenAPI:

  ```powershell
  uv run python -c "from app.main import app; o=app.openapi(); p=o['paths']; u=sorted(k for k in p if k.startswith('/api/v1/medications')); print(u); print(sorted({t for k in u for m in p[k].values() for t in m.get('tags', [])})); print(all(m.get('security') and m.get('summary') and m.get('description') for k in u for m in p[k].values())); print('content' in p['/api/v1/medications']['get']['responses']['200'], 'content' in p['/api/v1/medications']['post']['responses']['201']); print(sorted(p['/api/v1/medications']['post']['responses'])); print(sorted(p['/api/v1/medications/{medication_id}']['delete']['responses']))"
  ```

  imprime:

  ```text
  ['/api/v1/medications', '/api/v1/medications/{medication_id}']
  ['medications']
  True
  True True
  ['201', '401', '403', '404', '409', '422']
  ['204', '401', '403', '404', '422']
  ```

  Además, `GET /api/v1/medications` declara `['200', '401', '403', '422']`, `GET /{id}` declara `['200', '401', '403', '404', '422']` y `PUT` declara `['200', '401', '403', '404', '409', '422']`.

- [ ] **CA-29** `[shell]` Sin cambios de esquema ni deuda de lint: `uv run alembic check` imprime `No new upgrade operations detected.`, `git status --porcelain alembic/` no imprime nada, `uv run ruff check .` sin errores y `uv run ruff format --check .` sin cambios pendientes.
- [ ] **CA-30** `[shell]` `uv run pytest -q` en verde, incluidos los tests de FEAT-002/003/004/007/008/009 (sin regresiones).

### Trazabilidad

| Requisito / fuente | Criterios |
|---|---|
| RF-07 / US-20 / E3 (CRUD de medicamentos) | CA-4 a CA-24 |
| US-21 (consulta con ubicación: búsqueda, dosis, estante/tramo/celda vía `location_id`) | CA-7, CA-8, CA-10 |
| US-22 (integridad: catálogos activos; no borrar con visitas) | CA-14, CA-15, CA-24 |
| CA-CRUD 1–2 (listado paginado, `items/total/page/size/pages`) | CA-4, CA-5, CA-9 |
| CA-CRUD 3 (`201`, validación, `409` duplicados) | CA-11 a CA-16, CA-25 |
| CA-CRUD 4 (edición `200`, conserva `created_at`) | CA-18 |
| CA-CRUD 5 (baja lógica, sin borrado) | CA-22 a CA-24 |
| CA-CRUD 6 (`200`/`404` por ID) | CA-3, CA-17 |
| CA-CRUD 8 (escritura solo ADMIN, `403` a médico) | CA-1, CA-21, CA-23 |
| CA-CRUD 9 (validaciones y mensajes en español) | CA-5, CA-7, CA-8, CA-13 a CA-15 |
| CA-CRUD 10 (Swagger + pruebas) | CA-26 a CA-30 |
| §4 matriz (`/medications` CRUD ADMIN / GET MEDICO) | CA-1, CA-2, CA-17, CA-21, CA-23 |
| §8 (`POST` + única `(description,brand_id,dose)` + no crear con inactivos) | CA-11, CA-12, CA-14, CA-15, CA-19, CA-20, CA-25 |
| RF-17 (activar/desactivar sin eliminar) | CA-19, CA-22 a CA-24 |

## Plan de pruebas

### Aislamiento

Se reutiliza la infraestructura de FEAT-002/FEAT-004 sin cambios:

1. `db_conn` (transacción revertida) y `db_session` (`join_transaction_mode="create_savepoint"`) de `tests/conftest.py`; ninguna escritura sobrevive al test.
2. `auth_client` / `auth_client_factory` sobrescriben `get_session` con `db_session`; limpiar en `finally` (ya lo hace la fixture).
3. Los usuarios se crean con `tests/auth_support.create_auth_user` y los tokens con `tests/auth_support.token_for(user_id, role=...)` + `auth_headers`. Se necesita un `ADMIN` actor y un `MEDICO` lector por test; no se depende de datos preexistentes (`total` se compara contra `SELECT count(*) FROM medications` en la transacción).
4. Los catálogos prerrequisito se crean por API con el `ADMIN` actor (valida de paso FEAT-007/008/009) o por SQL directo con `db_conn`; las lecturas de verificación usan `db_conn` (misma conexión ⇒ ve los cambios no confirmados de `db_session`).
5. No se usan datos personales reales: descripciones y dosis sintéticas con `unique_suffix()` (p. ej. `f"Med {unique_suffix()}"`, `f"{n} mg"`); los casos con `%`, `_`, `\` usan sufijos fijos controlados.
6. La `visit_medication` de CA-24 se inserta por SQL directo con `visit_id`/`patient_id`/`doctor_id` existentes o también insertados por SQL; todo dentro de la transacción revertida.
7. `get_login_rate_limiter()` no se toca en estos tests (ningún CA hace login salvo verificación opcional de que el actor sigue activo; máximo 1 login por test si se usa).

### Unitarias (sin base de datos)

| # | Test | CA |
|---|---|---|
| U-1 | `MedicationCreate`: los seis campos (cinco obligatorios + default `is_active=True`), `strip` y 1..100 en `description`/`dose`, FK ≥ 1 | CA-13 |
| U-2 | `MedicationUpdate`: los seis campos obligatorios, `is_active` acepta `false`, textos y FK con las mismas reglas | CA-21 |
| U-3 | `MedicationRead`: serializa exactamente las siete claves planas, sin anidados | CA-10 |
| U-4 | Catálogo de `sort`: las 16 variantes válidas (8 campos × 2 sentidos) y el rechazo de inválidas (incluida la cadena vacía) | CA-6 |
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
| I-6 | Los 8 campos de `sort` en ambos sentidos, `NULLS LAST`, desempate e inválidos | CA-6 |
| I-7 | `search` en `description` o `dose`, case-insensitive, parcial, escapado de comodines y longitud | CA-7 |
| I-8 | Filtros `is_active` + `drug_type_id` + `brand_id` + `location_id`, combinación y FK inválidos en query | CA-8 |
| I-9 | Sin resultados: `pages == 0` | CA-9 |
| I-10 | Forma exacta de `item` en las cuatro respuestas con medicamento | CA-10, CA-17 |
| I-11 | Creación feliz activa/inactiva | CA-11 |
| I-12 | Unicidad compuesta `CITEXT` (capitalización, espacios, otra marca ⇒ `201`, otra dosis ⇒ `201`) | CA-12 |
| I-13 | Validaciones de `MedicationCreate` y normalización `strip` | CA-13 |
| I-14 | FK inexistentes ⇒ `404` del catálogo (3 casos + orden) | CA-14 |
| I-15 | FK inactivos ⇒ `422` con `field` (3 casos + precedencia 404 → 422) | CA-15 |
| I-16 | Log de creación con `medication_id`/`actor_id` | CA-16 |
| I-17 | Detalle activo/inactivo, `422` de path y lectura `MEDICO` | CA-17 |
| I-18 | `PUT` feliz con conservación de `created_at` y refresco de `updated_at` | CA-18 |
| I-19 | `PUT` reactiva/desactiva y cambia FK (activos ⇒ `200`, inexistente ⇒ `404`, inactivo ⇒ `422`) | CA-19 |
| I-20 | `PUT` duplicados ajenos vs. propios (incluido solo-`is_active`) | CA-20 |
| I-21 | `PUT` errores de body, precedencia `404` de ruta y `403` de `MEDICO` | CA-21 |
| I-22 | `DELETE` feliz, idempotente, `404` y `403` de `MEDICO` | CA-22, CA-23 |
| I-23 | `DELETE` de medicamento con visitas asociadas | CA-24 |
| I-24 | Envelope uniforme por cada código del catálogo | CA-26 |

### Integridad

| # | Test | CA |
|---|---|---|
| C-1 | Traducción de `IntegrityError` por nombre de restricción (`uq_medications_identity`) | CA-25 |

### Logs, OpenAPI y calidad

| # | Test | CA |
|---|---|---|
| S-1 | Los tres eventos con `medication_id`/`actor_id` y sin secretos ni PHI | CA-16, CA-27 |
| O-1 | OpenAPI: paths, tag, seguridad, `summary`/`description` y códigos | CA-28 |
| Q-1 | `alembic check` y `git status --porcelain alembic/` | CA-29 |
| Q-2 | `ruff check` y `ruff format --check` | CA-29 |
| Q-3 | `pytest -q` completo | CA-30 |

## Riesgos y dependencias

| ID | Riesgo | Impacto | Mitigación / decisión |
|---|---|---|---|
| **R-1** | La unicidad es compuesta `(description, brand_id, dose)` con dos columnas `CITEXT`: un implementer que clone FEAT-008 sin adaptar podría imponer unicidad solo por `description` o comparar `dose` case-sensitive. | Alto | Reglas 17 y 22, D-6/D-8, CA-12/CA-20/CA-25; el repositorio expone `find_medication_by_identity`, no `find_by_description`. |
| **R-2** | La validación de FK en dos niveles (404 inexistente / 422 inactivo) con precedencia entre tres catálogos es la parte más propensa a errores de orden (p. ej. comprobar unicidad antes que FK, o devolver 404 del medicamento después de validar FK en el `PUT`). | Medio | Regla 23 y D-10 fijan el orden total; CA-14/CA-15/CA-21 lo verifican caso por caso, incluida la precedencia cruzada. |
| **R-3** | `PUT` es reemplazo completo de seis campos: un cliente que envíe solo `dose` recibe `422`; no hay bloqueo optimista (last write wins). | Bajo | D-1; el frontend hace `GET` antes de `PUT`. El volumen y la concurrencia de edición del catálogo son mínimos. |
| **R-4** | Desactivar un medicamento no impide su uso hasta que la feature de visitas aplique "medicamentos deben estar activos"; temporalmente se puede prescribir con un medicamento inactivo. | Medio | D-7; la regla del §11 se implementa en US-30. El listado permite filtrar `is_active=true` desde el primer día. |
| **R-5** | `ILIKE` en `OR` sobre `description`+`dose` con comodines escapados: un escape mal construido convertiría `%` en comodín o rompería con `\`. | Bajo | Escapado explícito de `\`, `%` y `_` con `escape="\\"` aplicado una vez al término y reutilizado en ambas condiciones; el trigram `ix_medications_description_trgm` ya cubre `description`; CA-7 lo verifica en ambos campos y con los tres caracteres especiales. |
| **R-6** | `sort` por FK (`drug_type_id`, `brand_id`, `location_id`) ordena por id interno, no por nombre del catálogo; puede sorprender al frontend que espera orden alfabético por marca. | Bajo | Catálogo cerrado en D-3; CA-6 verifica ambos sentidos. Si US-21 exige orden alfabético por marca/ubicación, se añade en una feature posterior (p. ej. `sort=brand`) sin romper el contrato actual. |

**Dependencias**

- **FEAT-001 (`done`)**: FastAPI, sesión async, logging con redacción, envelope y middlewares.
- **FEAT-003 (`done`)**: tabla `medications` (`CITEXT` × 2, `uq_medications_identity`, `is_active` default `true`, FK `RESTRICT` × 3, índices FK + trigram), tablas `drug_types`/`brands`/`locations`.
- **FEAT-002 (`done`)**: `get_current_user`, `require_role` (incluye `require_role("ADMIN", "MEDICO")` — ambos roles están en `ALLOWED_ROLES`), envelope `{detail, code, errors}`.
- **FEAT-004 (`done`, patrón)**: paginación (`page/size/search/is_active/sort`), `_validation_field` (D-13), `204` idempotente (D-2), `clock_timestamp()` (D-14), estructura routes → services → repositories.
- **FEAT-007/008/009 (`done`, plantilla directa + prerrequisito)**: el implementer clona `locations` (unicidad compuesta, `OR` en `search`) y reutiliza sus repositorios para validar FK (`location_not_found` / `location_exists`, eventos `location.*`, sort default `description`); sin ellos no hay FK válidos que referenciar.
- **Bloquea**: la feature de visitas (US-30/US-22: "medicamentos deben estar activos", "no borrar con visitas") necesita este CRUD para poblar y validar `medication_id`.
- **No modifica** `app/core/config.py`, `app/api/deps.py`, `app/core/security.py`, `app/models/`, `alembic/` ni dependencias de `pyproject.toml`.

**Datos sensibles**

- `description`/`dose` son datos de catálogo (p. ej. "Acetaminofén", "500 mg"), no datos de paciente; aun así no se registran en logs (D-11).
- Los tests usan descripciones y dosis sintéticas dentro de una transacción revertida; no se usan datos personales reales.
- `audit_log` no se escribe en esta feature (RF-22 fuera de v1); el rastro queda en los eventos estructurados.

## Preguntas abiertas

1. **¿`dose` es texto libre o debe tener formato estructurado** (cantidad + unidad, p. ej. validar `"500 mg"` contra un patrón o catálogo de unidades)? La spec lo trata como texto libre 1..100 (igual que `description`), coherente con el ejemplo del §8. **No bloquea**: el contrato propuesto es implementable y verificable (CA-13); un validador de formato posterior sería compatible si solo endurece casos hoy aceptados.
2. **¿Se confirma 1..100 tras `strip` para `description` y `dose`** (mismo patrón 1..100 confirmado por el humano para los 4 campos de FEAT-009)? **No bloquea**: el implementer usa 100 y el ajuste posterior es localizado (`Field(max_length=...)` + CA-13).

No hay más bloqueos: permisos (matriz §3/§4), forma del `POST` (§8), paginación (§6), unicidad compuesta case-insensitive (`uq_medications_identity` en FEAT-003), regla de catálogos inactivos (§8, decidida en D-10) y baja lógica (RF-17/CA-CRUD 5) están definidos en las fuentes.

## Historial

- 2026-10-22 — creada por spec-author a partir de `DispensarioMedicoDocs/01-requerimientos-y-backlog.md` (RF-07, RF-17, US-20/US-21/US-22, E3, §3, §7) y `DispensarioMedicoDocs/03-api-rest.md` (§2, §4–§6, §8); `app/models/medication.py`, `app/api/v1/routes/drug_types.py`, `app/services/drug_type_service.py`, `app/schemas/drug_type.py`, `app/core/exceptions.py` y el patrón de `docs/features/FEAT-007.md`, `FEAT-008.md` y `FEAT-009.md`.
