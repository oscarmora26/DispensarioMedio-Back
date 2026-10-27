"""shelves_cells_schema

Revision ID: f39f66eed513
Revises: cd578a0a3215
Create Date: 2026-10-27 00:15:28.452395

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f39f66eed513"
down_revision: str | Sequence[str] | None = "cd578a0a3215"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Reemplaza `locations` por `shelves` + `cells`."""
    # 1. Crear shelves
    op.create_table(
        "shelves",
        sa.Column(
            "id", sa.BigInteger(), sa.Identity(always=False), primary_key=True, nullable=False
        ),
        sa.Column("name", postgresql.CITEXT(), nullable=False),
        sa.Column("description", postgresql.CITEXT(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("name", name="uq_shelves_name"),
    )
    # 2. Crear cells
    op.create_table(
        "cells",
        sa.Column(
            "id", sa.BigInteger(), sa.Identity(always=False), primary_key=True, nullable=False
        ),
        sa.Column(
            "shelf_id",
            sa.BigInteger(),
            sa.ForeignKey("shelves.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("section", postgresql.CITEXT(), nullable=False),
        sa.Column("cell", postgresql.CITEXT(), nullable=False),
        sa.Column("description", postgresql.CITEXT(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("shelf_id", "section", "cell", name="uq_cells_position"),
    )
    op.create_index("ix_cells_shelf_id", "cells", ["shelf_id"])
    # 3. Migrar datos: un `shelves` por cada `locations.shelf` distinto y una
    # `cells` por cada `locations`; despues se reapunta `medications.location_id`.
    op.execute("""
        -- Crear shelves a partir de locations distinct shelf
        INSERT INTO shelves (name, description, is_active, created_at, updated_at)
        SELECT DISTINCT ON (shelf) shelf, NULL, true, now(), now()
        FROM locations
        ORDER BY shelf, id;
    """)
    op.execute("""
        -- Crear cells a partir de locations, mapeando shelf_id
        INSERT INTO cells (shelf_id, section, cell, description, is_active, created_at, updated_at)
        SELECT s.id, l.section, l.cell, l.description, l.is_active, l.created_at, l.updated_at
        FROM locations l
        JOIN shelves s ON s.name = l.shelf;
    """)
    # 4. Actualizar medications.location_id: mapear por (shelf, section, cell) -> nuevo cell id
    op.execute("""
        UPDATE medications m
        SET location_id = c.id
        FROM locations l
        JOIN shelves s ON s.name = l.shelf
        JOIN cells c ON c.shelf_id = s.id AND c.section = l.section AND c.cell = l.cell
        WHERE m.location_id = l.id;
    """)
    # 5. Eliminar FK de medications y luego locations
    op.drop_constraint("fk_medications_location_id_locations", "medications", type_="foreignkey")
    op.drop_table("locations")
    # Re-crear FK apuntando a cells
    op.create_foreign_key(
        "fk_medications_location_id_cells",
        "medications",
        "cells",
        ["location_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Recrea `locations` y elimina `cells`/`shelves`.

    El orden importa: hay que soltar la FK de `medications` antes de eliminar
    `cells` (PostgreSQL rechaza el `DROP TABLE` mientras otro objeto dependa de
    ella) y volver a crearla apuntando a `locations`, ya que la columna sigue
    siendo NOT NULL.
    """
    op.create_table(
        "locations",
        sa.Column(
            "id", sa.BigInteger(), sa.Identity(always=False), primary_key=True, nullable=False
        ),
        sa.Column("description", postgresql.CITEXT(), nullable=False),
        sa.Column("shelf", postgresql.CITEXT(), nullable=False),
        sa.Column("section", postgresql.CITEXT(), nullable=False),
        sa.Column("cell", postgresql.CITEXT(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("shelf", "section", "cell", name="uq_locations_position"),
    )
    op.drop_constraint("fk_medications_location_id_cells", "medications", type_="foreignkey")
    op.drop_table("cells")
    op.drop_table("shelves")
    op.create_foreign_key(
        "fk_medications_location_id_locations",
        "medications",
        "locations",
        ["location_id"],
        ["id"],
        ondelete="RESTRICT",
    )
