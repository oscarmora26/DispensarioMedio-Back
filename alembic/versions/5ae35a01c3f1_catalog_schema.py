"""catalog_schema

Revision ID: 5ae35a01c3f1
Revises: 2eccca47c7f3
Create Date: 2026-10-21 02:24:47.152690

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "5ae35a01c3f1"
down_revision: str | Sequence[str] | None = "2eccca47c7f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Crea los catalogos `drug_types`, `brands`, `locations` y `medications`."""
    op.create_table(
        "drug_types",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("description", postgresql.CITEXT(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_drug_types"),
        sa.UniqueConstraint("description", name="uq_drug_types_description"),
    )

    op.create_table(
        "brands",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("description", postgresql.CITEXT(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_brands"),
        sa.UniqueConstraint("description", name="uq_brands_description"),
    )

    op.create_table(
        "locations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("description", postgresql.CITEXT(), nullable=False),
        sa.Column("shelf", postgresql.CITEXT(), nullable=False),
        sa.Column("section", postgresql.CITEXT(), nullable=False),
        sa.Column("cell", postgresql.CITEXT(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_locations"),
        sa.UniqueConstraint("shelf", "section", "cell", name="uq_locations_position"),
    )

    op.create_table(
        "medications",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("description", postgresql.CITEXT(), nullable=False),
        sa.Column("drug_type_id", sa.BigInteger(), nullable=False),
        sa.Column("brand_id", sa.BigInteger(), nullable=False),
        sa.Column("location_id", sa.BigInteger(), nullable=False),
        sa.Column("dose", postgresql.CITEXT(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_medications"),
        sa.UniqueConstraint("description", "brand_id", "dose", name="uq_medications_identity"),
        sa.ForeignKeyConstraint(
            ["drug_type_id"],
            ["drug_types.id"],
            name="fk_medications_drug_type_id_drug_types",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["brand_id"],
            ["brands.id"],
            name="fk_medications_brand_id_brands",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["locations.id"],
            name="fk_medications_location_id_locations",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_medications_drug_type_id", "medications", ["drug_type_id"], unique=False)
    op.create_index("ix_medications_brand_id", "medications", ["brand_id"], unique=False)
    op.create_index("ix_medications_location_id", "medications", ["location_id"], unique=False)
    op.execute(
        "CREATE INDEX ix_medications_description_trgm "
        "ON medications USING gin ((description::text) gin_trgm_ops)"
    )


def downgrade() -> None:
    """Elimina los catalogos en orden inverso de dependencias."""
    op.execute("DROP INDEX ix_medications_description_trgm")
    op.drop_index("ix_medications_location_id", table_name="medications")
    op.drop_index("ix_medications_brand_id", table_name="medications")
    op.drop_index("ix_medications_drug_type_id", table_name="medications")
    op.drop_table("medications")

    op.drop_table("locations")
    op.drop_table("brands")
    op.drop_table("drug_types")
