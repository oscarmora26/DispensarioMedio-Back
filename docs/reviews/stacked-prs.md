# Plan de PRs encadenados

**Fecha:** 2026-10-27
**Motivo:** el trabajo acumulado en `main` (14 commits) excede el tamano recomendado de un PR (~400 lineas
revisables). Se apila por feature para proteger el foco de revision: cada PR es una unidad tematica
cerrada, con sus tests y su documentacion.

**Nada de esto se ha subido.** El `git push` y la creacion de PRs son decisiones humanas (prohibido para
agentes por `AGENTS.md`).

## Stack

Cada rama parte de la anterior. Los commits ya existen; no se reescribio historia.

| # | Rama | Base | Commits | Diff vs base |
|---|---|---|---|---|
| 1 | `feat/FEAT-011-pacientes` | `main` | `a97fc94`, `5c39f1e` | 15 archivos, +2658 |
| 2 | `feat/FEAT-012-medicos` | `feat/FEAT-011-pacientes` | `7a82565`, `2066dd0`, `a28cf9e` | 16 archivos, +2611 |
| 3 | `feat/FEAT-013-visitas` | `feat/FEAT-012-medicos` | `adf0e55`, `05343d8`, `8e54f88` | 18 archivos, +4143 |
| 4 | `feat/FEAT-014-reportes` | `feat/FEAT-013-visitas` | `f8b64e5`, `9f13c49`, `cc40019` | 16 archivos, +2502 |
| 5 | `feat/FEAT-015-propuesta-observabilidad` | `feat/FEAT-014-reportes` | `42f191c` | 2 archivos, +69 |
| 6 | `feat/FEAT-016-busqueda-acentos` | `feat/FEAT-015-propuesta-observabilidad` | `2092415` | 17 archivos, +281 / -20 |
| 7 | `feat/FEAT-017-shelves-cells` | `feat/FEAT-016-busqueda-acentos` | `0191268` | 50 archivos, +2718 / -2445 |
| 8 | `feat/FEAT-017-api-separada` | `feat/FEAT-017-shelves-cells` | `9364c31` | 20 archivos, +2345 / -546 |
| 9 | `feat/docs-stack-y-vigencia` | `feat/FEAT-017-api-separada` | `9687684` | 9 archivos, +docs (solo documentacion) |
| 10 | `feat/FEAT-018-doctor-user` | `feat/docs-stack-y-vigencia` | `f7ee56b` | 8 archivos: `user` de solo lectura en `/doctors` |
| 11 | `feat/FEAT-019-visitas-permisos` | `feat/FEAT-018-doctor-user` | `4d2a8c2` | 11 archivos: ADMIN solo lectura en visitas, MEDICO escribe las propias |

### Foco de revision por PR

| # | Que mira el reviewer |
|---|---|
| 1 | CRUD de pacientes, validacion de cedula/carnet, busqueda |
| 2 | CRUD de medicos, asociacion usuario-medico, bloqueo de baja con visitas |
| 3 | Registro/edicion/anulacion de visitas, propiedad por medico, auditoria |
| 4 | Reporte JSON/CSV, filtros, zona horaria, export |
| 5 | Solo documentacion (propuesta de observabilidad) |
| 6 | Extension `unaccent` y busqueda insensible a acentos en 7 listados |
| 7 | Modelos `shelves`/`cells`, migracion de datos, `medications.cell_id`, contrato de `/shelves` |
| 8 | Separacion de `/cells`, validaciones de posicion y `PUT /cells` (fix de `updated_at`) |
| 9 | Plan de PRs encadenados y notas de vigencia en las specs historicas (sin codigo) |
| 10 | Exposicion de la cuenta del medico en `/doctors` (solo lectura, sin email) |
| 11 | Inversion de permisos de visitas: **modifica RF-12**; revisar con foco en autorizacion y filtro de propiedad |

## Comandos (a ejecutar por el humano)

Publicar primero el tramo completo, y crear cada PR con su base explicita. Los PRs 2..8 se abren como
borrador (`--draft`) hasta que su base este mergeada.

```bash
git push -u origin main
git push -u origin feat/FEAT-011-pacientes feat/FEAT-012-medicos feat/FEAT-013-visitas \
                   feat/FEAT-014-reportes feat/FEAT-015-propuesta-observabilidad \
                   feat/FEAT-016-busqueda-acentos feat/FEAT-017-shelves-cells \
                   feat/FEAT-017-api-separada

gh pr create --base main                                  --head feat/FEAT-011-pacientes                --title "feat(FEAT-011): gestión de pacientes" --body-file docs/reviews/stacked-prs.md
gh pr create --base feat/FEAT-011-pacientes               --head feat/FEAT-012-medicos                  --title "feat(FEAT-012): gestión de médicos" --draft
gh pr create --base feat/FEAT-012-medicos                 --head feat/FEAT-013-visitas                  --title "feat(FEAT-013): registro y gestión de visitas" --draft
gh pr create --base feat/FEAT-013-visitas                 --head feat/FEAT-014-reportes                 --title "feat(FEAT-014): reporte de visitas JSON/CSV" --draft
gh pr create --base feat/FEAT-014-reportes                --head feat/FEAT-015-propuesta-observabilidad --title "docs(FEAT-015): propuesta de observabilidad" --draft
gh pr create --base feat/FEAT-015-propuesta-observabilidad --head feat/FEAT-016-busqueda-acentos        --title "feat(FEAT-016): búsqueda insensible a acentos" --draft
gh pr create --base feat/FEAT-016-busqueda-acentos        --head feat/FEAT-017-shelves-cells            --title "feat(FEAT-017): shelves y cells; medications.cell_id" --draft
gh pr create --base feat/FEAT-017-shelves-cells           --head feat/FEAT-017-api-separada             --title "feat(FEAT-017): separar API de shelves y cells" --draft
gh pr create --base feat/FEAT-017-api-separada            --head feat/docs-stack-y-vigencia              --title "docs: ramas apiladas y vigencia de specs historicas" --draft
```

## Reglas de merge

1. Mergear en orden ascendente; nunca un PR con su base sin mergear.
2. Al mergear el PR N, GitHub retargetea el N+1 a `main` o hay que cambiar su base y marcarlo listo.
3. Cada PR llega con su spec en `docs/features/FEAT-0NN.md` y su informe de revision cuando exista.
4. Los PRs 7 y 8 son un cambio de contrato: `/api/v1/locations` deja de existir y `medications.location_id`
   pasa a `cell_id`. Coordinar con el despliegue del frontend.

## Riesgos conocidos del stack

- El PR 1 incluye commits de documentacion de FEAT-015 en una rama llamada FEAT-011: no es un error, el
  commit `42f191c` quedo separado como PR 5 para que la revision de codigo no cargue con documentacion.
- Los PRs 7 y 8 tocan la misma area (`shelves`/`cells`); 8 depende de 7. Revisar 7 con foco en la migracion
  y 8 con foco en las validaciones y el fix de `updated_at`.
- El PR 9 es solo documentacion y puede mergearse en cualquier momento despues del 8.
