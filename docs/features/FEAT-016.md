# FEAT-016 — Búsqueda insensible a acentos en listados con `search`

**Estado:** in_progress
**Fecha:** 2026-10-27
**Pedido del humano (verbatim):** "Quiero que no tomes en cuanta los acentos al realizar las busquedas" → aprobado con "Realiza el cambio".
**Alcance:** los 7 listados con parámetro `search`: `/drug-types`, `/brands`, `/locations`, `/medications`, `/patients`, `/doctors`, `/users`.

## Contexto

Hoy cada repositorio aplica `Columna.ilike(pattern, escape="\\")` sobre columnas `CITEXT`: la búsqueda es case-insensitive pero **sensible a acentos** — `capsula` no encuentra `Cápsula`. La extensión `unaccent` está disponible en la imagen `postgres:17-alpine` pero no creada (migración `18a65a470e8f_extensions` crea solo `citext` y `pg_trgm`).

## Reglas

- **R-1** El parámetro `search` ignora acentos en ambos lados: `capsula` encuentra `Cápsula` y `cápsula` encuentra `Capsula`.
- **R-2** Se conserva el comportamiento actual: case-insensitive, coincidencia parcial (`%term%`), escape literal de `%`/`_`/`\`, y filtros por caracteres especiales intactos.
- **R-3** Aplica a todos los campos buscables de cada listado (ver tabla). Campos sin acentos en la práctica (cédula, carnet, email, dosis) reciben el mismo tratamiento por uniformidad de patrón.
- **R-4** Sin cambios de contrato: mismo parámetro, mismos códigos de respuesta, mismo envelope de error.
- **R-5** La extensión se crea por migración Alembic (no manual); `downgrade` la elimina.

## Decisiones

- **D-1** Implementación con `func.unaccent(columna).ilike(func.unaccent(pattern), escape="\\")` en los 7 repositorios. `unaccent()` es `STABLE` (no indexable directamente), pero los listados ya hacen scan por el `%` inicial: no hay regresión de performance en catálogos de este tamaño.
- **D-2** Migración `unaccent_extension` con `CREATE EXTENSION IF NOT EXISTS unaccent` / downgrade `DROP EXTENSION IF EXISTS unaccent`, encadenada a `42de9c3ddd3a` (head actual). Debe aplicarse a la base de desarrollo (5433) y a la de tests (5434).
- **D-3** No se tocan rutas, schemas ni servicios: el cambio vive solo en la capa de repositorio.

## Campos por listado

| Listado | Campos con `search` |
|---|---|
| `/drug-types` | `description` |
| `/brands` | `description` |
| `/locations` | `description`, `shelf`, `section`, `cell` |
| `/medications` | `description`, `dose` |
| `/patients` | `name`, `cedula`, `carnet` |
| `/doctors` | `name`, `cedula` |
| `/users` | `username`, `email` |

## Criterios de aceptación

- [ ] **CA-1** `[test]` `/drug-types`: creado `Cápsula <tag>`, `search=capsula <tag>` (sin acento) lo encuentra; `search` con acento encuentra valor almacenado sin acento.
- [ ] **CA-2** `[test]` `/brands`: misma regla sobre `description`.
- [ ] **CA-3** `[test]` `/locations`: `search` sin acento encuentra por `description` acentuada.
- [ ] **CA-4** `[test]` `/medications`: `search` sin acento encuentra por `description` acentuada.
- [ ] **CA-5** `[test]` `/patients`: `search` sin acento encuentra por `name` acentuado (p. ej. `José` ⇄ `Jose`).
- [ ] **CA-6** `[test]` `/doctors`: `search` sin acento encuentra por `name` acentuado.
- [ ] **CA-7** `[test]` `/users`: `search` sin acento encuentra por `username`/`email` acentuado si existiera.
- [ ] **CA-8** `[test]` Los tests de búsqueda existentes (case-insensitive, `%`/`_` literales, espacios) siguen verdes sin modificación: la regla es aditiva.
- [ ] **CA-9** Migración aplicada en ambas bases; `alembic check` sin diferencias; suite completa verde; `ruff check`/`format --check` limpios.

## Estrategia de commits

Un commit: migración + repositorios + tests + spec + feature-list (`feat(FEAT-016): ...`).
