-- truncate_all.sql
-- Vacia TODAS las tablas de negocio de la base `dispensario` y reinicia
-- las secuencias identity. NO toca `alembic_version` (estado de migraciones).
--
-- IRREVERSIBLE: ejecutar solo con backup o en datos descartables.
-- Uso:
--   docker compose exec -T db psql -U dispensario -d dispensario -f - < scripts/truncate_all.sql

BEGIN;

-- RESTART IDENTITY: reinicia los contadores identity (id vuelve a 1).
-- CASCADE: necesario porque las FK son ON DELETE RESTRICT; al incluir
-- todas las tablas relacionadas en la misma sentencia no viola ninguna FK.
TRUNCATE TABLE
    audit_log,
    visit_medications,
    visits,
    medications,
    patients,
    doctors,
    users,
    drug_types,
    brands,
    locations,
    roles
RESTART IDENTITY CASCADE;

-- Re-semilla idempotente de roles base (misma SQL de la migracion
-- 42de9c3ddd3a_seed_roles); sin roles no hay login posible.
INSERT INTO roles (name, description) VALUES
    ('ADMIN', 'Administrador del sistema'),
    ('MEDICO', 'Medico del dispensario')
ON CONFLICT (name) DO NOTHING;

COMMIT;
