"""unaccent_extension

Revision ID: cd578a0a3215
Revises: 42de9c3ddd3a
Create Date: 2026-10-26 22:29:37.605690

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cd578a0a3215"
down_revision: str | Sequence[str] | None = "42de9c3ddd3a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Crea la extension `unaccent` para busquedas insensibles a acentos."""
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")


def downgrade() -> None:
    """Elimina la extension `unaccent`."""
    op.execute("DROP EXTENSION IF EXISTS unaccent")
