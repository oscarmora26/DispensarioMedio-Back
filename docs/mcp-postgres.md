# MCP de PostgreSQL (Postgres MCP Pro)

Servidor MCP que da al equipo acceso de **solo lectura** a la base local para inspeccionar esquema,
ejecutar `EXPLAIN` y revisar salud/índices. Configurado en `.opencode/opencode.json`.

## Configuración

```json
{
  "mcp": {
    "postgres": {
      "type": "local",
      "command": [
        "docker",
        "run",
        "-i",
        "--rm",
        "-e",
        "DATABASE_URI",
        "crystaldba/postgres-mcp",
        "--access-mode=restricted"
      ],
      "environment": {
        "DATABASE_URI": "postgresql://dispensario:dispensario@localhost:5433/dispensario"
      },
      "enabled": true,
      "timeout": 60000
    }
  }
}
```

- Imagen: `crystaldba/postgres-mcp` (Postgres MCP Pro).
- `--access-mode=restricted`: transacciones de solo lectura con límite de tiempo. Para tareas de
  escritura en local, cambiar a `--access-mode=unrestricted`.
- El `ENTRYPOINT` de la imagen remapea `localhost` → `host.docker.internal`, así que funciona con el
  PostgreSQL de `docker compose` publicado en el puerto `5433`.
- `DATABASE_URI` usa el driver `psycopg` (`postgresql://`), **no** `postgresql+asyncpg://` como
  `DATABASE_URL` de la app.

## Herramientas expuestas

| Tool | Uso |
|---|---|
| `list_schemas` | Esquemas disponibles |
| `list_objects` | Tablas, vistas, secuencias y extensiones de un esquema |
| `get_object_details` | Columnas, constraints e índices de un objeto |
| `execute_sql` | SQL de solo lectura |
| `explain_query` | Plan de ejecución (+ índices hipotéticos) |
| `get_top_queries` | Consultas más costosas (`pg_stat_statements`) |
| `analyze_db_health` | Salud de índices, vacuum, secuencias, conexiones |
| `analyze_workload_indexes` / `analyze_query_indexes` | Recomendación de índices |

`get_top_queries`, `analyze_workload_indexes` y parte de `analyze_db_health` requieren las
extensiones `pg_stat_statements` y `hypopg`; sin ellas el resto de tools funciona igual.

## Troubleshooting

### `Network is unreachable` / `connection is bad` en bucle

Síntoma en los logs del contenedor:

```text
WARNING  error connecting in 'pool-1': connection is bad: connection to server at
         "fdc4:f303:9324::254", port 5433 failed: Network is unreachable
```

Causa más frecuente: **el contenedor de PostgreSQL no está corriendo** (Docker Desktop se reinició, se
hizo `docker compose down`, etc.). El MCP arranca igual y reintenta; no es un problema de credenciales.

Checklist:

1. `docker compose up -d db` y confirmar `docker ps` → `dispensario-db ... (healthy)`.
2. Reintentar el MCP (si quedó un contenedor colgado: `docker ps -a --filter ancestor=crystaldba/postgres-mcp` y `docker rm -f <nombre>`; en OpenCode, reiniciar la sesión).
3. Verificar el puerto publicado: `docker compose ps db` debe mostrar `0.0.0.0:5433->5432/tcp`.

### Alternativa sin `host.docker.internal`

Si la resolución de `host.docker.internal` sigue fallando (típico cuando Docker Desktop prefiere IPv6),
conecta el MCP a la **red de Compose** y usa el nombre del servicio, sin depender del puerto publicado:

```json
"command": [
  "docker", "run", "-i", "--rm",
  "--network", "dispensariomedicoback_default",
  "-e", "DATABASE_URI",
  "crystaldba/postgres-mcp",
  "--access-mode=restricted"
],
"environment": {
  "DATABASE_URI": "postgresql://dispensario:dispensario@db:5432/dispensario"
}
```

El nombre de red es `<carpeta-del-proyecto>_default` (`docker network ls` para confirmarlo). Verificado:
conecta y evita el remapeo de `localhost`. Requiere que el stack de Compose exista.

## Verificación manual (sin OpenCode)

```powershell
docker run -i --rm -e DATABASE_URI=postgresql://dispensario:dispensario@localhost:5433/dispensario `
  crystaldba/postgres-mcp --access-mode=restricted
```

Con stdin conectado a un JSON-RPC (`initialize` → `notifications/initialized` → `tools/list`) responde
`Successfully connected to database and initialized connection pool` y lista las 9 tools.

## Notas

- Se descartó `uvx postgres-mcp` (PyPI 0.3.0): falla al importar con `mcp>=2` (`Server` ya no expone
  `list_resources`) y arrastra incompatibilidades con `pydantic`. La imagen Docker trae su propio
  entorno y funciona.
- Las tools MCP aparecen al **reiniciar la sesión** de OpenCode después de editar la configuración.
- El MCP no sustituye a Alembic: los cambios de esquema se hacen con migraciones versionadas.
