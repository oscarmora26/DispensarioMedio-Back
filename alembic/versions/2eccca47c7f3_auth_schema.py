"""auth_schema

Revision ID: 2eccca47c7f3
Revises: 18a65a470e8f
Create Date: 2026-10-21 02:24:44.700007

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.naming import conv

# revision identifiers, used by Alembic.
revision: str = "2eccca47c7f3"
down_revision: str | Sequence[str] | None = "18a65a470e8f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Crea `roles`, `doctors` y `users`."""
    op.create_table(
        "roles",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.String(length=20), nullable=False),
        sa.Column("description", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_roles"),
        sa.UniqueConstraint("name", name="uq_roles_name"),
        sa.CheckConstraint("name IN ('ADMIN', 'MEDICO')", name=conv("ck_roles_name_allowed")),
    )

    op.create_table(
        "doctors",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("cedula", sa.String(length=11), nullable=False),
        sa.Column("lab_shift", sa.String(length=50), nullable=False),
        sa.Column("specialty", sa.String(length=100), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_doctors"),
        sa.UniqueConstraint("cedula", name="uq_doctors_cedula"),
        sa.CheckConstraint("cedula ~ '^[0-9]{11}$'", name=conv("ck_doctors_cedula_format")),
    )
    op.create_index(
        "ix_doctors_name_trgm",
        "doctors",
        ["name"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )

    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("username", postgresql.CITEXT(), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column("doctor_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("username", name="uq_users_username"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.UniqueConstraint("doctor_id", name="uq_users_doctor"),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            name="fk_users_role_id_roles",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["doctor_id"],
            ["doctors.id"],
            name="fk_users_doctor_id_doctors",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_users_role_id", "users", ["role_id"], unique=False)


def downgrade() -> None:
    """Elimina `users`, `doctors` y `roles`."""
    op.drop_index("ix_users_role_id", table_name="users")
    op.drop_table("users")

    op.drop_index("ix_doctors_name_trgm", table_name="doctors")
    op.drop_table("doctors")

    op.drop_table("roles")
