# FEAT-013 — Registro y gestión de visitas: registrar, listar, detalle, editar y anular

> **Nota de vigencia (2026-10-27).** Esta especificación describe el contrato tal como se aprobó en su
> fecha. Desde **FEAT-019** los permisos de escritura de visitas se invirtieron: el `ADMIN` pasó a
> **solo lectura** (se modificó RF-12) y el `MEDICO` realiza todas las acciones sobre **sus propias**
> visitas. El contrato vigente está en [FEAT-019](FEAT-019.md).

**Estado:** `approved` (aprobada explícitamente por el humano el 2026-10-24, en bloque con FEAT-011…FEAT-014)
**Fecha:** 2026-10-24
**Alcance:** `POST/GET /api/v1/visits`, `GET/PUT /api/v1/visits/{visit_id}`, `POST /api/v1/visits/{visit_id}/annul`, con lectura y registro para `ADMIN` y `MEDICO` (el médico solo sus propias visitas, US-31) y edición/anulación solo para `ADMIN`, reutilizando autenticación JWT, envelope `{detail, code, errors}` de FEAT-002/FEAT-004 y los catálogos de FEAT-010 (medicamentos), FEAT-011 (pacientes) y FEAT-012 (médicos).
**Fuente de verdad funcional:** `DispensarioMedicoDocs/01-requerimientos-y-backlog.md` RF-11, RF-12, RF-13, RF-14, RF-17, US-30/US-31/US-32, E6, §3 (matriz: visitas CRUD+anular ADMIN / crear y consultar propias MEDICO) y §7 (CA-CRUD); `DispensarioMedicoDocs/03-api-rest.md` §2 (paginación, envelope, códigos), §4 (matriz `/visits`: CRUD + anular ADMIN / POST + GET propias MEDICO), §5 (endpoints), §6 (parámetros de listado) y §11 (`POST /visits`, `GET /visits` con filtros, `POST /visits/{id}/annul` + reglas: médico del token para `MEDICO`, `doctor_id` indicable por `ADMIN`, paciente y medicamentos activos, fecha no futura, síntomas obligatorios, medicamentos opcionales, visita anulada no editable).

## Contexto

Las tablas `visits` y `visit_medications` ya existen (FEAT-003): `visits` con `doctor_id`/`patient_id` (FK RESTRICT), `visited_at` con zona horaria, `symptoms VARCHAR(2000) NOT NULL`, `recommendations VARCHAR(2000) NULL`, `status` con `CHECK status_allowed` (`REGISTRADA`/`ANULADA`), `created_by` FK users, campos de anulación (`annulled_by`, `annulled_at`, `annulment_reason VARCHAR(500)`) con `CHECK annulment_consistency`, e índices de consulta (`ix_visits_doctor_visited`, `ix_visits_patient_visited`, `ix_visits_visited`, `ix_visits_status_visited`); `visit_medications` con `uq_visit_medications` (`visit_id, medication_id`), `CHECK quantity_positive` (`quantity IS NULL OR quantity > 0`), `instructions VARCHAR(255)` y FK `medications` RESTRICT / `visits` CASCADE.

Hoy **no existe ninguna vía HTTP para registrar, consultar, editar ni anular visitas**. Sin esta feature, RF-11…RF-14, US-30/US-31/US-32 (Sprint 7) quedan sin implementar, y FEAT-014 (reportes) no tiene datos que reportar.

## Alcance

- `app/schemas/visit.py` (nuevo): `VisitMedicationInput`, `VisitCreate`, `VisitUpdate`, `VisitAnnulRequest`, `VisitRead` (detalle, forma del §11), `VisitListItem` (forma resumida, D-8), `VisitListResponse`, `VisitAnnulResponse`, `VISIT_STATUS_VALUES`, `SORT_FIELDS`, `SORT_PATTERN`.
- `app/repositories/visit_repository.py` (nuevo): listado paginado con filtros combinables (doctor, paciente, rango de fechas, medicamento, estado, texto), detalle con medicamentos, y conteo de filas sin límite.
- `app/services/visit_service.py` (nuevo): `list_visits`, `get_visit`, `create_visit`, `update_visit`, `annul_visit`; resolución del médico desde el token, validación de referencias activas y traducción de `IntegrityError` (`uq_visit_medications`, `quantity_positive`, `status_allowed`, `annulment_consistency`).
- `app/api/v1/routes/visits.py` (nuevo) con tag `visits`, registrado en `app/api/v1/router.py`.
- Reutilización de: `app/api/deps.py`, `app/core/exceptions.py`, `app/models/visit.py`, `app/models/visit_medication.py`, `app/repositories/doctor_repository.py` (`get_doctor_by_id`), `app/repositories/patient_repository.py` (`get_patient_by_id`), `app/repositories/medication_repository.py` (consulta de medicamento), y los patrones de FEAT-011/FEAT-012.
- Cinco endpoints descritos en "Contrato de API".
- Logs estructurados `visit.created`, `visit.updated`, `visit.annulled`.
- Documentación OpenAPI completa (tag, `summary`, `description`, `response_model`, códigos).
- Sin migraciones: `uv run alembic check` debe seguir limpio.

### Estructura de archivos resultante

```text
app/
├── api/v1/
│   ├── router.py                 # modificado: incluye visits
│   └── routes/visits.py          # nuevo
├── repositories/
│   └── visit_repository.py       # nuevo
├── schemas/visit.py              # nuevo
└── services/visit_service.py     # nuevo
```

## Fuera de alcance

- Reportes y exportación CSV/PDF: FEAT-014.
- Recetas médicas formales, citas, inventario/stock: excluidos de v1 (§2 del documento de requerimientos).
- "Guardar y registrar otra": comportamiento de UX del frontend Angular.
- Cambio del estado por `PUT` (reactivar una visita anulada): no existe; la anulación es irreversible por API.
- Frontend Angular.

## Reglas de negocio

### Autorización y alcance

1. Lectura y registro (`GET /visits`, `GET /visits/{id}`, `POST /visits`): rol `ADMIN` **o** `MEDICO` mediante `require_role("ADMIN", "MEDICO")`; el rol se lee de la base (FEAT-002 regla 12).
2. Edición y anulación (`PUT /visits/{id}`, `POST /visits/{id}/annul`): rol `ADMIN` exclusivamente; un `MEDICO` autenticado recibe `403` `forbidden` en ambas, sin efectos en la base.
3. Sin cabecera, con esquema distinto de `Bearer`, con token inválido/expirado o de un usuario inexistente ⇒ `401` (`not_authenticated` o `invalid_token`).
4. Un usuario desactivado pierde el acceso en la siguiente request (`403` `inactive_user`), incluso con token vigente.
5. Todos los errores usan el envelope global `{"detail": str, "code": str, "errors": [{"field","message"}]}`. Códigos nuevos: `visit_not_found` (404), `visit_annulled` (409, editar anulada), `visit_already_annulled` (409, anular anulada), `doctor_not_associated` (403, MEDICO sin médico). Reutilizados: `patient_not_found`, `doctor_not_found`, `medication_not_found` (404), `not_authenticated`/`invalid_token` (401), `inactive_user`/`forbidden` (403), `validation_error` (422).

### Resolución del médico (US-30, RF-10)

6. Con token `MEDICO`, el médico de la visita es **siempre** `user.doctor_id` (del token, leído de la base). Si el body trae `doctor_id` distinto del propio ⇒ `422` `validation_error` con `field == "doctor_id"` y mensaje `"El médico se obtiene del token"`. Si el usuario `MEDICO` no tiene `doctor_id` asociado ⇒ `403` `doctor_not_associated` con mensaje `"El usuario no tiene un médico asociado"` (US-27: sin asociación no registra visitas).
7. Con token `ADMIN`, `doctor_id` es **obligatorio** en el body (`POST` y `PUT`); ausente ⇒ `422` `validation_error` con `field == "doctor_id"` y mensaje `"El médico es obligatorio"`. Un `doctor_id` inexistente ⇒ `404` `doctor_not_found`; inactivo ⇒ `422` con `field == "doctor_id"` y mensaje `"El médico debe estar activo"` (D-5). El mismo control de médico activo aplica al camino del token: un `MEDICO` cuyo médico asociado está inactivo recibe el mismo `422` (aclaración de implementación, ver Historial).

### Validación de campos

8. `patient_id`: entero positivo obligatorio. Inexistente ⇒ `404` `patient_not_found`; paciente inactivo ⇒ `422` `validation_error` con `field == "patient_id"` y mensaje `"El paciente debe estar activo"` (§11: el paciente debe estar activo).
9. `visited_at`: datetime con zona horaria obligatorio. Un valor **futuro** (instante mayor que `now()` UTC en el momento de validar) ⇒ `422` con `field == "visited_at"` y mensaje `"La fecha de la visita no puede ser futura"` (§11). Se aceptan fechas pasadas (incluido hoy).
10. `symptoms`: se recorta; obligatorio, entre 3 y 2000 caracteres (US-30). Vacío o menor a 3 ⇒ `422` con `field == "symptoms"` y mensaje `"Los síntomas deben tener entre 3 y 2000 caracteres"`; mayor a 2000 ⇒ `422` con el mismo `field` y mensaje `"Los síntomas no pueden superar 2000 caracteres"`.
11. `recommendations`: opcional; se recorta; vacío o solo espacios ⇒ `null`. Con contenido, máximo 2000 caracteres; mayor ⇒ `422` con `field == "recommendations"` y mensaje `"Las recomendaciones no pueden superar 2000 caracteres"` (US-30).
12. `medications`: lista opcional; ausente ⇒ `[]`; puede estar vacía (§11). Cada elemento tiene `medication_id` (entero positivo), `quantity` (decimal positivo opcional) e `instructions` (texto opcional, máximo 255). Un `medication_id` repetido dentro de la misma lista ⇒ `422` con `field == "medications"` y mensaje `"Medicamento duplicado en la visita"`. `quantity` presente y ≤ 0 ⇒ `422` con `field == "quantity"` y mensaje `"La cantidad debe ser mayor que cero"`. `instructions` mayor a 255 ⇒ `422` con `field == "instructions"`.
13. Referencias de medicamentos: `medication_id` inexistente ⇒ `404` `medication_not_found`; medicamento inactivo ⇒ `422` con `field == "medications"` y mensaje `"El medicamento debe estar activo"` (§11: los medicamentos deben estar activos).
14. Los mensajes de las reglas 6–13 van en español y viajan en `errors[0]` con el `field` indicado, sin prefijo `body`.

### Registro `POST /visits`

15. Body: `doctor_id` (regla 6/7), `patient_id`, `visited_at`, `symptoms`, `recommendations` (opcional), `medications` (opcional). Ejemplo literal del §11: `{"doctor_id": 5, "patient_id": 100, "visited_at": "2026-10-21T10:30:00-04:00", "symptoms": "Dolor de cabeza y fiebre", "recommendations": "Reposo e hidratación", "medications": [{"medication_id": 8, "quantity": 2, "instructions": "Cada 8 horas"}]}`.
16. `201` con la forma de detalle (D-7), `status == "REGISTRADA"`, `created_by == actor.id`, `created_at` presente. `annulled_by`/`annulled_at`/`annulment_reason` **no** aparecen en la respuesta.
17. Los detalles de medicación se guardan en `visit_medications`; `quantity` ausente se guarda como `null` (el `CHECK quantity_positive` lo admite).
18. Si el `commit` viola `uq_visit_medications`, `quantity_positive`, `status_allowed` o `annulment_consistency` (carrera o vía no prevista), se traduce al `422`/`409` correspondiente y la sesión queda usable (D-9).

### Listado `GET /visits`

19. Parámetros: `page` (≥1, defecto 1), `size` (1..100, defecto 20), `doctor_id` (solo ADMIN, regla 20), `patient_id` (opcional), `date_from` (opcional), `date_to` (opcional), `medication_id` (opcional), `status` (`REGISTRADA`|`ANULADA`, opcional), `q` (texto, máximo 200), `sort` (catálogo cerrado de D-6, defecto `-visited_at`).
20. `doctor_id` con token `MEDICO` ⇒ `422` con `field == "doctor_id"` y mensaje `"Solo el administrador puede filtrar por médico"` (§11: solo admin). Para `MEDICO` el filtro de propiedad **siempre** se aplica: `visits.doctor_id == user.doctor_id`, combinado en `AND` con los demás filtros (US-31, RF-13). Un `MEDICO` sin asociación ⇒ página vacía (`items: []`, `total: 0`, `pages: 0`) sin consulta.
21. `date_from`/`date_to`: datetimes; si son *naive* se asumen en `America/Santo_Domingo` y se convierten a UTC (RNF-12); si `date_from > date_to` (comparados en UTC) ⇒ `422` con `field == "date_from"` y mensaje `"La fecha inicial no puede ser mayor que la final"`. El rango se aplica sobre `visited_at` con inclusión de ambos extremos.
22. `medication_id`: filtra visitas que incluyen ese medicamento (exists en `visit_medications`).
23. `status`: catálogo cerrado; otro valor ⇒ `422` con `field == "status"`.
24. `q`: se recorta; vacío ⇒ sin filtro; con contenido, búsqueda case-insensitive y parcial en `symptoms` **o** `recommendations` (OR) con `ILIKE` escapado (`escape_like`). Buscar `%` encuentra solo visitas con `%` literal en esos textos.
25. Orden: campo del catálogo de D-6, ascendente por defecto y descendente con `-`. `visited_at` es el orden por defecto **descendente** (US-34: orden por fecha/hora descendente). Desempate estable: `visits.id DESC` salvo cuando el campo es `id`.
26. Respuesta: `{items, total, page, size, pages}` con cada item en la forma resumida (D-8). `pages == 0` cuando `total == 0`.

### Detalle `GET /visits/{visit_id}`

27. `visit_id` no entero ⇒ `422`; inexistente ⇒ `404` `visit_not_found` con mensaje `"La visita no existe"`.
28. `200` con la forma de detalle (D-7), incluida una visita anulada (con sus campos de anulación). Para `MEDICO`, una visita ajena (exista o no) ⇒ `404` `visit_not_found` (sin fuga de existencia, mismo criterio que FEAT-012 D-6).

### Edición `PUT /visits/{visit_id}`

29. Solo `ADMIN`. **Reemplazo completo** (D-10) de `doctor_id`, `patient_id`, `visited_at`, `symptoms`, `recommendations` (opcional; ausente ⇒ `null`) y `medications` (opcional; ausente ⇒ `[]`). Un body parcial sin `doctor_id`/`patient_id`/`visited_at`/`symptoms` ⇒ `422` con el campo ausente. `status`, `created_by` y los campos de anulación no se editan.
30. Una visita `ANULADA` ⇒ `409` `visit_annulled` con mensaje `"La visita anulada no se puede editar"` (§11), sin cambios en la base.
31. Se aplican las mismas validaciones 6–14. La lista de medicamentos se **reemplaza** por completo (los detalles retirados se borran por `delete-orphan`, D-10).
32. `200` con la forma de detalle (D-7). `created_at` se conserva; `updated_at` se refresca con `clock_timestamp()` (D-11).

### Anulación `POST /visits/{visit_id}/annul`

33. Solo `ADMIN`. Body: `reason` obligatorio; se recorta; vacío ⇒ `422` con `field == "reason"` y mensaje `"El motivo es obligatorio"`; máximo 500 caracteres (columna `VARCHAR(500)`).
34. Una visita ya `ANULADA` ⇒ `409` `visit_already_annulled` con mensaje `"La visita ya está anulada"` (la anulación es única y auditable, D-12).
35. Visita inexistente ⇒ `404` `visit_not_found`.
36. Efecto: `status = "ANULADA"`, `annulled_by = actor.id`, `annulled_at = clock_timestamp()`, `annulment_reason` recortado. Respuesta `200` con la forma literal del §11: `{id, status, annulled_at, annulled_by, annulment_reason}`. `updated_at` se refresca.

### Registro (logs)

37. Eventos `visit.created`, `visit.updated` y `visit.annulled` con `visit_id` y `actor_id`, y **nunca** `symptoms`, `recommendations`, `reason`, ni datos de paciente/médico (AGENTS.md: ningún dato clínico ni personal en logs).

## Supuestos

- SUP-1: la base ya tiene `visits` y `visit_medications` de FEAT-003 con sus constraints; no hay migraciones nuevas.
- SUP-2: los `CHECK` de la base (`status_allowed`, `annulment_consistency`, `quantity_positive`) ya imponen las mismas reglas; la API las valida antes y traduce las violaciones de carrera.
- SUP-3: `require_role` entrega el usuario con su rol cargado y `doctor_id` disponible (FEAT-004).
- SUP-4: los actores de prueba y catálogos de medicamentos/pacientes/médicos existen en la suite desde FEAT-010…FEAT-012.

## Decisiones técnicas y desviaciones documentadas

- D-1: para `MEDICO`, un `doctor_id` distinto del propio en el body se **rechaza** con `422` (no se ignora): "El médico se obtiene del token" y no es editable (US-30).
- D-2: el `doctor_id` del token no se usa como identidad; la identidad es `user.doctor_id` de la base (FEAT-002 regla 12 aplicada a médicos).
- D-3: fechas: la comparación "no futura" se hace en UTC con `datetime.now(UTC)`; las entradas *naive* de `visited_at`/`date_from`/`date_to` se asumen en `America/Santo_Domingo` y se convierten a UTC (RNF-12). Implementación: offset fijo UTC-4 etiquetado `America/Santo_Domingo` porque RD no tiene DST y `tzdata` no es dependencia del proyecto (semántica idéntica).
- D-4: `quantity` se modela como `Decimal` (columna `Numeric(10,2)`); la API lo acepta como número decimal, nunca `float` (AGENTS.md).
- D-5: un médico inactivo no puede recibir visitas nuevas (422), coherente con "el paciente y los medicamentos deben estar activos" aunque el §11 no lo explicite.
- D-6: catálogo cerrado de `sort`: `visited_at`, `created_at`, `id`, `status`. Defecto `-visited_at`. `SORT_PATTERN = r"^-?(?:visited_at|created_at|id|status)$"`.
- D-7: forma de detalle = la del ejemplo `201` del §11, con `doctor`/`patient` anidados y `medications` con `description` y `dose`; sin campos de anulación salvo en visitas anuladas, donde se añaden `annulled_at`, `annulled_by`, `annulment_reason`. Incluye `created_at`/`updated_at` (reglas 16 y 32) y oculta las claves de anulación solo cuando `annulled_at` es `null` mediante `model_serializer` (un `response_model_exclude_none` habría ocultado también `recommendations: null`, contra CA-14).
- D-8: forma resumida de lista (no definida en el doc): `{id, doctor: {id, name}, patient: {id, name, cedula}, visited_at, symptoms, status, created_by}` sin `medications` ni `recommendations`, para mantener el listado paginado barato (US-34: <500 ms con 10.000 visitas).
- D-9: traducción de `IntegrityError`: `uq_visit_medications` ⇒ 422 `medications` "Medicamento duplicado en la visita"; `quantity_positive` ⇒ 422 `quantity`; `status_allowed`/`annulment_consistency` ⇒ 422 `status` (solo alcanzable por vías no previstas). Tras el fallo, `rollback`.
- D-10: `PUT` reemplaza también la lista de medicamentos (los omitidos se eliminan de `visit_medications`); la ausencia de `medications`/`recommendations` los vacía. La colección vieja se vacía y se hace `flush` antes de asignar la nueva lista: sin ese orden, SQLAlchemy inserta los detalles nuevos antes de borrar los huérfanos y reutilizar un medicamento viola `uq_visit_medications`.
- D-11: `updated_at` con `func.clock_timestamp()` (misma desviación que FEAT-004 D-14 y FEAT-007…FEAT-012).
- D-12: la anulación no es idempotente: repetirla ⇒ `409` `visit_already_annulled`; `annulled_at`/`annulled_by` son inmutables tras la primera anulación.
- D-13: tag OpenAPI `visits`, prefijo `/visits`.

## Contrato de repositorio (nombres que usa el servicio)

```python
# app/repositories/visit_repository.py
SORT_COLUMNS: dict[str, InstrumentedAttribute[Any]]

async def get_visit_by_id(
    session: AsyncSession, visit_id: int, *, with_medications: bool = False
) -> Visit | None
async def list_visits(
    session: AsyncSession,
    *,
    page: int,
    size: int,
    doctor_id: int | None,
    patient_id: int | None,
    date_from: datetime | None,
    date_to: datetime | None,
    medication_id: int | None,
    status: str | None,
    q: str | None,
    sort_field: str,
    sort_desc: bool,
) -> tuple[list[Visit], int]
```

`list_visits` recibe los filtros ya normalizados (fechas en UTC) y aplica el filtro de propiedad con el mismo parámetro `doctor_id`. `get_visit_by_id(..., with_medications=True)` carga los detalles con `selectinload` para el detalle y el `PUT`.

## Contrato de API

### Autorización común

| Situación | Respuesta |
|---|---|
| Sin token / token inválido / expirado | `401` `not_authenticated` o `invalid_token` |
| Usuario inactivo | `403` `inactive_user` |
| `MEDICO` en `PUT`/`annul` | `403` `forbidden` |
| `MEDICO` sin médico asociado en `POST` | `403` `doctor_not_associated` |

### `POST /api/v1/visits`

```json
{
  "doctor_id": 5,
  "patient_id": 100,
  "visited_at": "2026-10-21T10:30:00-04:00",
  "symptoms": "Dolor de cabeza y fiebre",
  "recommendations": "Reposo e hidratación",
  "medications": [
    { "medication_id": 8, "quantity": 2, "instructions": "Cada 8 horas" }
  ]
}
```

`201` con la forma de D-7 (ejemplo literal del §11). Errores: `401`, `403`, `404` (`doctor_not_found`, `patient_not_found`, `medication_not_found`), `422`.

### `GET /api/v1/visits`

Query: `page`, `size`, `doctor_id` (solo ADMIN), `patient_id`, `date_from`, `date_to`, `medication_id`, `status`, `q`, `sort` (defecto `-visited_at`).

`200` con `{items, total, page, size, pages}` e items en la forma de D-8. Para `MEDICO`, siempre sus propias visitas. Errores: `401`, `403`, `422`.

### `GET /api/v1/visits/{visit_id}`

`200` con la forma de D-7 (incluye anuladas). `MEDICO`: solo propias (ajena ⇒ `404`). Errores: `401`, `403`, `404`, `422`.

### `PUT /api/v1/visits/{visit_id}`

Body como `POST` (D-10). `200` con la forma de D-7. Visita anulada ⇒ `409` `visit_annulled`. Errores: `401`, `403`, `404`, `409`, `422`.

### `POST /api/v1/visits/{visit_id}/annul`

Body: `{"reason": "Visita registrada por error"}`.

`200`:

```json
{
  "id": 500,
  "status": "ANULADA",
  "annulled_at": "2026-10-21T11:00:00-04:00",
  "annulled_by": 1,
  "annulment_reason": "Visita registrada por error"
}
```

Errores: `401`, `403`, `404` `visit_not_found`, `409` `visit_already_annulled`, `422`.

### OpenAPI

- `tags=["visits"]`, `summary` y `description` en cada endpoint, en español.
- `response_model` en todos; códigos documentados en `responses`.
- `SORT_PATTERN` reutilizado en el `Query` del `sort`.

## Criterios de aceptación

### Autorización

- CA-1: `POST` y `GET` responden `200`/`201` con token `ADMIN` y con token `MEDICO`; `PUT` y `annul` responden `403` `forbidden` con `MEDICO` sin cambios en la base.
- CA-2: los cinco endpoints responden `401` sin cabecera y con token inválido.
- CA-3: usuario desactivado ⇒ `403` `inactive_user` en los cinco endpoints.

### Médico del token

- CA-4: `POST` con `MEDICO` crea la visita con `doctor_id == user.doctor_id`; un `doctor_id` distinto en el body ⇒ `422` `field == "doctor_id"` mensaje "El médico se obtiene del token".
- CA-5: `POST` con `MEDICO` sin asociación ⇒ `403` `doctor_not_associated`.
- CA-6: `POST`/`PUT` con `ADMIN` sin `doctor_id` ⇒ `422` `field == "doctor_id"` mensaje "El médico es obligatorio"; médico inexistente ⇒ `404`; inactivo ⇒ `422`.

### Validación

- CA-7: `visited_at` futura ⇒ `422` `field == "visited_at"`; pasada (incluido hoy) ⇒ aceptada.
- CA-8: `symptoms` vacío o <3 ⇒ `422` mensaje de rango; >2000 ⇒ `422` mensaje de máximo; 3..2000 ⇒ aceptado.
- CA-9: `recommendations` ausente/vacía ⇒ `null`; >2000 ⇒ `422`; ≤2000 ⇒ aceptada.
- CA-10: paciente inexistente ⇒ `404` `patient_not_found`; inactivo ⇒ `422` `field == "patient_id"`.
- CA-11: medicamento inexistente ⇒ `404` `medication_not_found`; inactivo ⇒ `422` `field == "medications"`; duplicado en la lista ⇒ `422`; `quantity` ≤ 0 ⇒ `422` `field == "quantity"`; `instructions` >255 ⇒ `422`.

### Creación

- CA-12: `POST` válido sin `medications` ⇒ `201` con forma D-7, `status REGISTRADA`, `created_by == actor.id`, `created_at` presente y `medications == []`.
- CA-13: `POST` con medicamentos ⇒ `201` con los detalles (`medication_id`, `description`, `dose`, `quantity`, `instructions`) y filas en `visit_medications`.
- CA-14: `POST` con `recommendations` de solo espacios ⇒ `recommendations: null`.
- CA-15: respuesta sin `annulled_by`/`annulled_at`/`annulment_reason`.

### Listado

- CA-16: envelope `items/total/page/size/pages`; `pages == 0` con `total == 0`; `page` fuera de rango ⇒ `items == []`.
- CA-17: filtro `doctor_id` (ADMIN), `patient_id`, `status`, `medication_id` y combinaciones en `AND`.
- CA-18: `q` busca en síntomas o recomendaciones (case-insensitive); `q="%"` no devuelve todas.
- CA-19: `date_from > date_to` ⇒ `422`; rango inclusivo sobre `visited_at`.
- CA-20: orden por defecto `-visited_at`; catálogo `visited_at`/`created_at`/`id`/`status`; `sort` inválido ⇒ `422`.
- CA-21: `MEDICO` ve solo sus visitas aunque envíe filtros; con `doctor_id` ⇒ `422`; sin asociación ⇒ página vacía.
- CA-22: cada item tiene exactamente las claves de D-8.

### Detalle

- CA-23: detalle ⇒ `200` forma D-7 con medicamentos; anulada ⇒ incluye `annulled_at`/`annulled_by`/`annulment_reason`.
- CA-24: inexistente ⇒ `404` `visit_not_found`; no entero ⇒ `422`; `MEDICO` con visita ajena ⇒ `404`.

### Edición

- CA-25: `PUT` completo ⇒ `200`, `created_at` conservado, `updated_at` refrescado y `created_by` intacto.
- CA-26: `PUT` que reemplaza la lista de medicamentos borra los detalles retirados.
- CA-27: `PUT` de visita anulada ⇒ `409` `visit_annulled` sin cambios.
- CA-28: `PUT` parcial ⇒ `422` con el campo ausente; validaciones 6–14 se aplican igual que en `POST`.

### Anulación

- CA-29: `annul` válido ⇒ `200` forma del §11; la visita queda `ANULADA` con `annulled_by == actor.id` y motivo recortado.
- CA-30: motivo vacío ⇒ `422`; >500 ⇒ `422`; ya anulada ⇒ `409` `visit_already_annulled`; inexistente ⇒ `404`.
- CA-31: una visita anulada ya no es editable (`409` `visit_annulled`) ni re-anulable.

### Integridad y errores

- CA-32: envelope uniforme `{detail, code, errors}` sin `details`.
- CA-33: violación de `uq_visit_medications` en el `commit` ⇒ `422` traducido y sesión usable.

### Logs, OpenAPI y calidad

- CA-34: eventos `visit.created`/`visit.updated`/`visit.annulled` con `visit_id`/`actor_id` y sin textos clínicos ni datos personales (regla 37).
- CA-35: `/openapi.json` incluye las cinco rutas con tag `visits`, `summary`, `description` y códigos.
- CA-36: `ruff check`/`format --check` limpios; `alembic check` sin diferencias; suite completa en verde.

### Trazabilidad

| CA | Fuente |
|---|---|
| CA-1…CA-3 | §4 matriz `/visits`, FEAT-002 reglas 11–13 |
| CA-4…CA-6 | §11 (médico del token), US-30, US-27, RF-10 |
| CA-7…CA-11 | §11, US-30, RF-18 |
| CA-12…CA-15 | §11 (`POST /visits`), US-30 |
| CA-16…CA-22 | §11 (`GET /visits`), US-31, US-34, RF-13/14/15 |
| CA-23…CA-24 | §5 (`GET /visits/{id}`), US-31 |
| CA-25…CA-28 | §5 (`PUT /visits/{id}`), US-32, RF-12 |
| CA-29…CA-31 | §11 (`annul`), US-32, RF-12 |
| CA-32…CA-36 | §2, §14; Definition of Done |

## Plan de pruebas

### Aislamiento

Un test = una transacción con savepoint deshecho al final (patrón FEAT-007…FEAT-012). Los tests de integración usan `tests/visits_support.py` con helpers de creación directa de visitas, médicos, pacientes, medicamentos y usuarios.

### Unitarias (sin base de datos)

- Validadores de `symptoms`/`recommendations`/`medications`/`quantity`/`instructions`/`reason` (CA-7…CA-11, CA-30).
- `parse_sort`/`calculate_pages` (CA-16, CA-20).
- Normalización de fechas naive a `America/Santo_Domingo` (D-3).

### Integración (PostgreSQL + httpx)

- Registro con `ADMIN` y con `MEDICO` (CA-4…CA-6, CA-12…CA-15).
- Autorización y sin token (CA-1…CA-3).
- Listado con filtros combinados, `q`, rango de fechas, orden y propiedad (CA-16…CA-22).
- Detalle y errores (CA-23, CA-24).
- Edición y reemplazo de medicamentos (CA-25…CA-28).
- Anulación (CA-29…CA-31).
- Traducción de `IntegrityError` y sesión usable (CA-33).
- OpenAPI (CA-35).

### Logs y calidad

`caplog`/`structlog`: los tres eventos existen, llevan `visit_id`/`actor_id` y no contienen `symptoms`, `recommendations` ni el motivo de anulación (regla 37).

## Riesgos y dependencias

- Depende de FEAT-010 (medicamentos), FEAT-011 (pacientes), FEAT-012 (médicos y asociación) y FEAT-002/004 (auth y envelope). Sin migraciones.
- FEAT-014 (reportes) depende de esta feature para los datos.
- Riesgo de datos bajo: solo se añaden endpoints; no se modifican tablas. Las escrituras son registros nuevos (no destructivos); la anulación es auditada e inmutable.

## Preguntas abiertas

Ninguna bloqueante. Resueltas por el documento de API o por decisión documentada: médico inactivo no recibe visitas (D-5), forma resumida del listado (D-8), `PUT` reemplaza la lista de medicamentos (D-10), anulación no idempotente (D-12). La pregunta §14.1 (¿el médico edita sus propias visitas?) queda resuelta por la matriz: no, solo ADMIN edita y anula.

## Historial

| Fecha | Cambio |
|---|---|
| 2026-10-24 | Spec inicial para implementación (aprobación humana del bloque FEAT-011…014). |
| 2026-10-24 | Aclaraciones de implementación (writer): D-3 offset fijo UTC-4; regla 7 extendida al camino del token (médico asociado inactivo ⇒ 422); D-7 con `model_serializer` para ocultar solo claves de anulación nulas; D-10 con `flush` previo al reemplazo de detalles; CA-33 no alcanzable por API (la validación rechaza duplicados antes del commit) y se fija con test unitario del traductor + end-to-end de `quantity_positive` por redondeo (0.004 → 0.00). |
