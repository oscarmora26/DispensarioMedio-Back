"""seed_roles

Revision ID: 42de9c3ddd3a
Revises: b9c060201177
Create Date: 2026-10-21 02:24:54.334887

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "42de9c3ddd3a"
down_revision: str | Sequence[str] | None = "b9c060201177"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_ROLES_SQL = """
INSERT INTO roles (name, description) VALUES
    ('ADMIN', 'Administrador del sistema'),
    ('MEDICO', 'Medico del dispensario')
ON CONFLICT (name) DO NOTHING
"""

UNSEED_ROLES_SQL = """
DELETE FROM roles r
WHERE r.name IN ('ADMIN', 'MEDICO')
  AND NOT EXISTS (SELECT 1 FROM users u WHERE u.role_id = r.id)
"""


def upgrade() -> None:
    """Siembra los roles `ADMIN` y `MEDICO` de forma idempotente."""
    op.execute(SEED_ROLES_SQL)


def downgrade() -> None:
    """Borra los roles sembrados que no esten en uso."""
    op.execute(UNSEED_ROLES_SQL)
