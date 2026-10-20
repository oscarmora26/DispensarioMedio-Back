# Feature List — Dispensario Médico Backend

> **Fuente de verdad del backlog.** Solo el `team-lead` actualiza estados en este archivo.
> **Solo el humano aprueba** la transición `proposed → approved`.
> Se ejecuta **una feature a la vez**.

## Estados

| Estado | Significado | Quién lo asigna |
|---|---|---|
| `proposed` | Idea registrada, sin aprobar | `team-lead` / humano |
| `approved` | Autorizada para implementarse | **solo humano** |
| `in_progress` | En implementación | `team-lead` |
| `in_review` | Implementada, esperando veredicto del reviewer | `team-lead` |
| `changes_requested` | Revisión con hallazgos bloqueantes/mayores | `team-lead` |
| `done` | Revisada, testeada y aprobada | `team-lead` |
| `blocked` | Dependencia externa, ambigüedad o 3 iteraciones sin converger | `team-lead` |
| `rejected` | Descartada | **solo humano** |

## Feature activa

Ninguna. *(El `team-lead` escribe aquí el ID en `in_progress`/`in_review` y lo limpia al cerrar.)*

## Backlog

| ID | Feature | Estado | Spec | Depende de | Actualizado |
|---|---|---|---|---|---|
| FEAT-001 | Fundaciones de la API: scaffold, config, logging, PostgreSQL y Docker | `done` | [FEAT-001](docs/features/FEAT-001.md) | — | 2026-10-20 |
| FEAT-002 | Autenticación y gestión de usuarios (JWT + roles) | `proposed` | pendiente | FEAT-001 | 2026-10-20 |

> FEAT-001 se ejecutó por instrucción directa del humano como línea base del proyecto, fuera del ciclo spec → implement → review.

## Cómo aprobar una feature

El humano edita este archivo y cambia el estado de `proposed` a `approved`, o se lo pide explícitamente al `team-lead`. Sin esa acción, el equipo no empieza a trabajar.

## Reglas de transición

```text
proposed ──(humano)──> approved
approved ──(team-lead)──> in_progress ──> in_review ──> done
                                              └──> changes_requested ──> in_progress
cualquiera ──(team-lead)──> blocked
proposed/approved ──(humano)──> rejected
```

- No se salta de `proposed` a `in_progress`.
- No se marca `done` sin veredicto `APPROVED` del reviewer.
- Una feature `blocked` necesita intervención humana para desbloquearse.
- Máximo 3 ciclos de `changes_requested`; después pasa a `blocked`.

## Historial

- 2026-10-20 — FEAT-001 registrada como `done` (bootstrap por instrucción humana).
- 2026-10-20 — FEAT-002 registrada como `proposed`.
