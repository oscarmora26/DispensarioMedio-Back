"""audit_schema

Revision ID: b9c060201177
Revises: d0663853f377
Create Date: 2026-10-21 02:24:51.903881

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.naming import conv

# revision identifiers, used by Alembic.
revision: str = "b9c060201177"
down_revision: str | Sequence[str] | None = "d0663853f377"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Crea la tabla `audit_log`."""
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("entity", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("old_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
        sa.CheckConstraint(
            "action IN ('CREATE', 'UPDATE', 'DEACTIVATE', 'ANNUL', 'LOGIN')",
            name=conv("ck_audit_log_action_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_audit_log_user_id_users",
            ondelete="SET NULL",
        ),
    )


def downgrade() -> None:
    """Elimina la tabla `audit_log`."""
    op.drop_table("audit_log")
