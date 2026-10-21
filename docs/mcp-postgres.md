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
