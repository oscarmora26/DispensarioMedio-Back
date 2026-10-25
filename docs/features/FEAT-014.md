# FEAT-014 — Reporte de visitas (JSON/CSV) y exportación

**Estado:** `approved` (aprobada explícitamente por el humano el 2026-10-24, en bloque con FEAT-011…FEAT-014)
**Fecha:** 2026-10-24
**Alcance:** `GET /api/v1/reports/visits` (JSON por defecto, CSV opcional) y `GET /api/v1/reports/visits/export` (CSV) para `ADMIN` y `MEDICO` (el médico solo su propio reporte), reutilizando los filtros de FEAT-013, la autenticación JWT y el envelope de errores de FEAT-002/FEAT-004.
**Fuente de verdad funcional:** `DispensarioMedicoDocs/01-requerimientos-y-backlog.md` RF-15, RF-16, RF-21, US-34/US-35/US-36, E7, §3 (matriz: Reporte de visitas ADMIN todas / MEDICO propias) y RNF-04/RNF-12; `DispensarioMedicoDocs/03-api-rest.md` §2, §4 (matriz `/reports/visits`), §5 (endpoints) y §12 (`GET /reports/visits` con `date_from`/`date_to` obligatorios, filtros opcionales, resumen por médico; `GET /reports/visits/export` con `format csv|pdf`; reglas: `date_from <= date_to`, médico solo su propio reporte, generación < 5 s, zona horaria `America/Santo_Domingo`).

## Contexto

FEAT-013 ya expone `GET /visits` con filtros combinables (US-34 cubierta) y la infraestructura de visitas (`visits`, `visit_medications`, índices de consulta). Falta la capa de **reporte** (US-35) y **exportación** (US-36): un resumen agregado por médico con detalle paginado, en JSON y CSV.

## Alcance

- `app/schemas/report.py` (nuevo): `ReportFilters`, `ReportSummary`, `DoctorTotal`, `ReportResponse`, `REPORT_FORMAT_VALUES`, `REPORT_SORT_PATTERN`.
- `app/repositories/visit_repository.py` (ampliado): `summarize_visits` (totales por médico usando `_list_conditions`), reutiliza `list_visits` para el detalle.
- `app/services/report_service.py` (nuevo): `build_report` (JSON), `export_report_csv` (CSV con BOM), validación de rango obligatorio y de formato, filtro de propiedad del médico.
- `app/services/visit_service.py` (mínimo): exponer `to_list_item(visit)` público (envuelve `_to_list_item`) y `SANTO_DOMINGO`/`to_utc` ya públicos; ningún otro cambio.
- `app/api/v1/routes/reports.py` (nuevo) con tag `reports` y prefijo `/reports/visits`, registrado en `app/api/v1/router.py`.
- Dos endpoints descritos en "Contrato de API".
- Log estructurado `report.generated` con `actor_id`, filtros y `format`, **sin** textos clínicos ni datos personales.
- Documentación OpenAPI completa.
- Sin migraciones: `uv run alembic check` debe seguir limpio.

### Estructura de archivos resultante

```text
app/
├── api/v1/
│   ├── router.py                 # modificado: incluye reports
│   └── routes/reports.py         # nuevo
├── repositories/
│   └── visit_repository.py       # ampliado: summarize_visits
├── schemas/report.py             # nuevo
└── services/
    ├── report_service.py         # nuevo
    └── visit_service.py          # mínimo: to_list_item público
```

## Fuera de alcance

- PDF: **diferido a decisión humana** (pregunta abierta §14.2 del documento de requerimientos). En v1, `format=pdf` ⇒ `422` con mensaje explícito (D-1). No se añade dependencia de generación de PDF.
- Impresión desde el frontend: US-36 parcialmente diferida con el PDF.
- Cambios en `/visits` de FEAT-013 (la consulta por criterios ya está cubierta).
- Frontend Angular.

## Reglas de negocio

### Autorización

1. `GET /reports/visits` y `GET /reports/visits/export`: rol `ADMIN` **o** `MEDICO` mediante `require_role("ADMIN", "MEDICO")`; el rol se lee de la base.
2. Sin token/inválido ⇒ `401`; usuario desactivado ⇒ `403` `inactive_user`; envelope `{detail, code, errors}` en todos los errores.

### Rango de fechas (obligatorio, §12)

3. `date_from` y `date_to` son **obligatorios** en ambos endpoints; ausentes ⇒ `422` `validation_error` con `field == "date_from"` o `field == "date_to"` respectivamente.
4. `date_from > date_to` (comparados en UTC) ⇒ `422` con `field == "date_from"` y mensaje `"La fecha inicial no puede ser mayor que la final"` (mismo criterio y mensaje que FEAT-013 regla 21). El rango es **inclusivo** en ambos extremos sobre `visited_at`.
5. Entradas *naive* se asumen en `America/Santo_Domingo` (UTC-4 fijo, sin DST, D-2) y se convierten a UTC con `to_utc` de FEAT-013 (RNF-12).

### Filtros opcionales y propiedad (US-35)

6. `doctor_id` (solo ADMIN): filtra por médico. Un `MEDICO` que envía `doctor_id` ⇒ `422` con `field == "doctor_id"` y mensaje `"Solo el administrador puede filtrar por médico"`. Para `MEDICO` el filtro de propiedad se aplica **siempre**: `visits.doctor_id == user.doctor_id` (§12: el médico solo genera su propio reporte). Un `MEDICO` sin médico asociado ⇒ reporte vacío (`total_visits == 0`, `by_doctor == []`, `items == []`).
7. `patient_id` y `status` (`REGISTRADA`|`ANULADA`, cerrado; otro valor ⇒ `422` con `field == "status"`) se combinan en `AND` con el rango y la propiedad.
8. `doctor_id`/`patient_id` inexistentes son **filtros sin coincidencia**: resultado vacío, sin `404` (no son referencias del body).

### Formato (D-1)

9. `GET /reports/visits`: `format` opcional, `json` (defecto) o `csv`. `csv` devuelve el mismo CSV del export (D-6) con `Content-Type: text/csv`. `pdf` ⇒ `422` con `field == "format"` y mensaje `"El formato PDF no está disponible en la versión 1"`.
10. `GET /reports/visits/export`: `format` opcional, defecto `csv`; `pdf` ⇒ el mismo `422` de la regla 9.

### Respuesta JSON (D-3, D-4, D-5)

11. `GET /reports/visits` con `format=json` (o ausente) responde `200` con la forma extendida del §12 (D-3: añade envelope de paginación por RNF-06):

```json
{
  "filters": {
    "date_from": "2026-10-01",
    "date_to": "2026-10-31",
    "doctor_id": null,
    "patient_id": null,
    "status": "REGISTRADA"
  },
  "summary": {
    "total_visits": 120,
    "by_doctor": [
      { "doctor_id": 5, "doctor_name": "Dra. Ana Pérez", "total": 80 }
    ]
  },
  "items": [],
  "total": 0,
  "page": 1,
  "size": 20,
  "pages": 0
}
```

12. `filters` hace eco de los valores **tal como se recibieron** (cadenas de fecha originales, ids o `null`, `status` o `null`), sin normalizar (D-4).
13. `summary.total_visits` es el número de visitas que cumplen los filtros (coincide con `total` del envelope; redundancia del §12 que se conserva). `by_doctor` agrupa por `doctor_id` con el nombre actual del médico, ordenado por `total` descendente y desempate `doctor_name` ascendente.
14. `items` usa la forma resumida D-8 de FEAT-013 (`{id, doctor: {id, name}, patient: {id, name, cedula}, visited_at, symptoms, status, created_by}`); el detalle respeta `page` (≥1, defecto 1), `size` (1..100, defecto 20) y `sort` (catálogo de FEAT-013 D-6: `visited_at`, `created_at`, `id`, `status`; defecto `-visited_at`). `pages == 0` cuando `total == 0`.
15. Parámetros inválidos (`page < 1`, `size > 100`, `sort` fuera de catálogo, `status` fuera de catálogo) ⇒ `422` con el `field` del parámetro.

### Exportación CSV (D-6, §12)

16. `GET /reports/visits/export` (o `format=csv`) responde `200` `text/csv` con:
    - `Content-Disposition: attachment; filename="reporte_visitas_<AAAAMMDD>.csv"` con la fecha de generación en `America/Santo_Domingo`;
    - UTF-8 **con BOM** (`\ufeff`) para compatibilidad con Excel;
    - encabezado en español: `id,fecha,medico,cedula_medico,paciente,cedula_paciente,sintomas,recomendaciones,estado,creado_por` (D-5);
    - una fila por visita del reporte (mismos filtros y propiedad que el JSON), **sin paginación** (el CSV exporta el reporte completo, D-7), orden `-visited_at`;
    - fechas en ISO 8601, `recomendaciones` vacía cuando es `null`, quoting estándar del módulo `csv` (comas y comillas escapadas).
17. El CSV no incluye la cabecera `summary` ni `filters` (es solo el detalle exportable, §12).

### Registro (logs)

18. Evento `report.generated` con `actor_id`, `format`, `date_from`, `date_to`, `doctor_id`, `patient_id`, `status` (solo ids y filtros), y **nunca** síntomas, recomendaciones ni datos personales de pacientes/médicos (AGENTS.md).

## Supuestos

- SUP-1: FEAT-013 está `done`; `list_visits`, `_list_conditions`, `to_utc` y `SANTO_DOMINGO` existen y se reutilizan sin cambios de comportamiento.
- SUP-2: sin migraciones; los índices de FEAT-003 soportan los filtros del reporte (`ix_visits_doctor_visited`, `ix_visits_patient_visited`, `ix_visits_visited`, `ix_visits_status_visited`).
- SUP-3: el requisito RNF-04 (<5 s con 10.000 visitas) se satisface estructuralmente (agregado en una consulta por índice y detalle paginado); no se testea con carga en la suite.

## Decisiones técnicas y desviaciones documentadas

- D-1: PDF **diferido** (pregunta §14.2 no resuelta por el humano); `format=pdf` ⇒ `422` con mensaje explícito, en lugar de `501`, para mantener el envelope de validación uniforme.
- D-2: `America/Santo_Domingo` se implementa como UTC-4 fijo (RD sin DST, sin dependencia `tzdata`), igual que FEAT-013 D-3.
- D-3: el JSON añade el envelope de paginación (`total/page/size/pages`) a la forma del §12 porque RNF-06 exige listas paginadas; la forma del ejemplo del §12 queda contenida en `filters` + `summary` + `items`.
- D-4: `filters` es eco literal de la query (sin normalizar fechas), para que el cliente recupere exactamente lo que pidió.
- D-5: columnas del CSV en español y `snake_case` para `cedula_medico`/`cedula_paciente`/`creado_por`, coherentes con el resto del proyecto.
- D-6: `format=csv` en el endpoint JSON devuelve el mismo CSV del export (fiel a la tabla del §12, que lista `format: json|csv|pdf` en ambos).
- D-7: el CSV exporta el detalle completo sin paginación; el JSON es la vista paginada del mismo reporte.
- D-8: tag OpenAPI `reports`, prefijo `/reports/visits`.

## Contrato de repositorio (nombres que usa el servicio)

```python
# app/repositories/visit_repository.py (ampliado, reusa _list_conditions)
async def summarize_visits(
    session: AsyncSession,
    *,
    doctor_id: int | None,
    patient_id: int | None,
    date_from: datetime | None,
    date_to: datetime | None,
    status: str | None,
) -> tuple[int, list[tuple[int, str, int]]]
```

Devuelve `(total_visits, [(doctor_id, doctor_name, total), ...])` ordenado por `total DESC, doctor_name ASC`, agrupando con `func.count` y `join` a `doctors`. `list_visits` (existente) sigue sirviendo el detalle paginado.

## Contrato de API

### Autorización común

| Situación | Respuesta |
|---|---|
| Sin token / token inválido / expirado | `401` `not_authenticated` o `invalid_token` |
| Usuario inactivo | `403` `inactive_user` |

### `GET /api/v1/reports/visits`

Query: `date_from` (obligatorio), `date_to` (obligatorio), `doctor_id` (solo ADMIN), `patient_id`, `status`, `format` (`json` defecto | `csv` | `pdf` rechazado), `page`, `size`, `sort`.

`200` JSON con la forma de las reglas 11–14, o `200` `text/csv` con `format=csv` (reglas 16–17). Errores: `401`, `403`, `422`.

### `GET /api/v1/reports/visits/export`

Query: igual, con `format` (`csv` defecto). `200` `text/csv` con `Content-Disposition: attachment; filename="reporte_visitas_<AAAAMMDD>.csv"`. Errores: `401`, `403`, `422`.

### OpenAPI

- `tags=["reports"]`, `summary` y `description` en español, `responses` con códigos.
- `GET /reports/visits` documenta `response_model=ReportResponse` y `200` CSV como respuesta alternativa en la descripción.

## Criterios de aceptación

### Autorización

- CA-1: ambos endpoints responden `200` con token `ADMIN` y con token `MEDICO` (propio).
- CA-2: ambos responden `401` sin token y con token inválido; `403` `inactive_user` con usuario desactivado.

### Rango y validación

- CA-3: sin `date_from` o sin `date_to` ⇒ `422` con el campo correspondiente.
- CA-4: `date_from > date_to` ⇒ `422` `field == "date_from"` mensaje "La fecha inicial no puede ser mayor que la final"; rango inclusivo (visita exactamente en el borde cuenta); fechas naive asumidas en RD (UTC-4).
- CA-5: `status` fuera de `REGISTRADA`/`ANULADA` ⇒ `422`; `page < 1`, `size > 100`, `sort` inválido ⇒ `422` con su `field`.

### Propiedad y filtros

- CA-6: `MEDICO` ve solo sus visitas en `total_visits`, `by_doctor` e `items`; con `doctor_id` ⇒ `422`; sin asociación ⇒ reporte vacío.
- CA-7: `doctor_id` (ADMIN), `patient_id` y `status` filtran y combinan en `AND`.
- CA-8: `doctor_id`/`patient_id` inexistentes ⇒ reporte vacío sin `404`.

### Resumen y forma JSON

- CA-9: `filters` hace eco literal de la query (incluidos `null`).
- CA-10: `summary.total_visits` cuenta exacto; `by_doctor` agrupa con nombre actual del médico y orden `total DESC, doctor_name ASC`; `summary.total_visits == total`.
- CA-11: `items` con la forma D-8 exacta; `pages == 0` con `total == 0`; orden por defecto `-visited_at`.
- CA-12: `format=pdf` ⇒ `422` con mensaje "El formato PDF no está disponible en la versión 1"; `format=json` ⇒ JSON; ausente ⇒ JSON.

### CSV

- CA-13: export ⇒ `200` `text/csv` con BOM, encabezado español exacto, `Content-Disposition: attachment; filename="reporte_visitas_<AAAAMMDD>.csv"`.
- CA-14: filas correctas: fechas ISO, `recomendaciones` vacía si `null`, quoting con comas en síntomas; sin paginación (todas las visitas del reporte).
- CA-15: `format=csv` en `/reports/visits` devuelve el mismo CSV; el export respeta los mismos filtros y propiedad.

### Logs, OpenAPI y calidad

- CA-16: `report.generated` con `actor_id`, `format` y filtros, sin síntomas/recomendaciones ni datos personales.
- CA-17: `/openapi.json` incluye las dos rutas con tag `reports`, summaries y códigos.
- CA-18: `ruff check`/`format --check` limpios; `alembic check` sin diferencias; suite completa en verde.

### Trazabilidad

| CA | Fuente |
|---|---|
| CA-1…CA-2 | §4 matriz `/reports/visits`, FEAT-002 reglas 11–13 |
| CA-3…CA-5 | §12 (`date_from <= date_to`), §2/§6, US-35 |
| CA-6…CA-8 | §12 ("el médico solo genera su propio reporte"), §4, US-35 |
| CA-9…CA-12 | §12 (forma de respuesta), RNF-06, D-1 |
| CA-13…CA-15 | §12 (`export`, `format csv|pdf`), US-36, RF-21 |
| CA-16…CA-18 | AGENTS.md (logs), §14, Definition of Done |

## Plan de pruebas

### Aislamiento

Mismo patrón de FEAT-013 (`tests/db_support.py`, savepoints). `tests/reports_support.py` con helpers de visitas en rango con médicos/pacientes conocidos.

### Unitarias (sin base de datos)

- Validación de rango/status/format/sort (CA-3, CA-4, CA-5, CA-12).
- Serialización CSV con `csv` (quoting, BOM, encabezado, filename) (CA-13, CA-14).

### Integración (PostgreSQL + httpx)

- Reporte JSON con resumen y detalle (CA-9…CA-11).
- Propiedad del médico y filtros (CA-6…CA-8).
- Export CSV end-to-end con `Content-Disposition` (CA-13…CA-15).
- Autorización (CA-1, CA-2).
- OpenAPI (CA-17).

### Logs y calidad

`caplog`/`structlog`: `report.generated` sin textos clínicos (CA-16). Gates de CA-18.

## Riesgos y dependencias

- Depende de FEAT-013 (`visits`, filtros, `to_utc`) y FEAT-002/004 (auth y envelope). Sin migraciones.
- Riesgo de datos bajo: solo lectura; no se modifican filas.
- PDF fuera de v1 (D-1): si el humano aprueba PDF más adelante, se reabre esta feature o se crea una nueva.

## Preguntas abiertas

- §14.2 (¿PDF, Excel o ambos?): **pendiente de decisión humana**; v1 exporta CSV y rechaza `pdf` con `422` explícito (D-1). Registrado también en `feature-list.md`.

## Historial

| Fecha | Cambio |
|---|---|
| 2026-10-24 | Spec inicial para implementación (aprobación humana del bloque FEAT-011…014). |
| 2026-10-24 | Aclaraciones de implementación (writer): la columna `fecha` del CSV se emite en ISO 8601 convertida a `America/Santo_Domingo` (RNF-12); `/reports/visits/export` acepta `page`/`size`/`sort` pero no los aplica (D-7, queda explícito en la descripción OpenAPI); `date_from`/`date_to` se reciben como `str` y se parsean en el servicio para el eco literal de D-4; `summarize_visits` añade `doctor_id ASC` como tercer desempate; `status` se valida en la ruta (patrón, para OpenAPI) y en el servicio (catálogo, mensaje español). |
