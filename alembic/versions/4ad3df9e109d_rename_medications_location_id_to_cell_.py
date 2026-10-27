"""rename_medications_location_id_to_cell_id

Revision ID: 4ad3df9e109d
Revises: f39f66eed513
Create Date: 2026-10-27 01:49:31.614818

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4ad3df9e109d"
down_revision: str | Sequence[str] | None = "f39f66eed513"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Renombra `medications.location_id` a `cell_id`.

    El nombre viejo es herencia de la tabla `locations` eliminada en
    `f39f66eed513`; la columna ya apunta a `cells.id`, asi que se alinea el
    nombre con la entidad real junto con su indice y su constraint.
    """
    op.alter_column("medications", "location_id", new_column_name="cell_id")
    op.execute("ALTER INDEX ix_medications_location_id RENAME TO ix_medications_cell_id")
    op.execute(
        "ALTER TABLE medications RENAME CONSTRAINT "
        "fk_medications_location_id_cells TO fk_medications_cell_id_cells"
    )


def downgrade() -> None:
    """Restaura el nombre `location_id`."""
    op.execute(
        "ALTER TABLE medications RENAME CONSTRAINT "
        "fk_medications_cell_id_cells TO fk_medications_location_id_cells"
    )
    op.execute("ALTER INDEX ix_medications_cell_id RENAME TO ix_medications_location_id")
    op.alter_column("medications", "cell_id", new_column_name="location_id")
