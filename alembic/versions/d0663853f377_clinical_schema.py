"""clinical_schema

Revision ID: d0663853f377
Revises: 5ae35a01c3f1
Create Date: 2026-10-21 02:24:49.527822

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.naming import conv

# revision identifiers, used by Alembic.
revision: str = "d0663853f377"
down_revision: str | Sequence[str] | None = "5ae35a01c3f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Crea `patients`, `visits` y `visit_medications`."""
    op.create_table(
        "patients",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("cedula", sa.String(length=11), nullable=False),
        sa.Column("carnet", postgresql.CITEXT(), nullable=True),
        sa.Column("patient_type", sa.String(length=20), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_patients"),
        sa.UniqueConstraint("cedula", name="uq_patients_cedula"),
        sa.CheckConstraint("cedula ~ '^[0-9]{11}$'", name=conv("ck_patients_cedula_format")),
        sa.CheckConstraint(
            "patient_type IN ('ESTUDIANTE', 'EMPLEADO', 'PROFESOR', 'OTROS')",
            name=conv("ck_patients_patient_type_allowed"),
        ),
        sa.CheckConstraint(
            "(patient_type = 'ESTUDIANTE' AND carnet IS NOT NULL"
            " AND btrim(carnet) <> '') OR patient_type <> 'ESTUDIANTE'",
            name=conv("ck_patients_carnet_required_for_student"),
        ),
    )
    op.create_index(
        "uq_patients_carnet",
        "patients",
        ["carnet"],
        unique=True,
        postgresql_where=sa.text("carnet IS NOT NULL"),
    )
    op.create_index(
        "ix_patients_name_trgm",
        "patients",
        ["name"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )

    op.create_table(
        "visits",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("doctor_id", sa.BigInteger(), nullable=False),
        sa.Column("patient_id", sa.BigInteger(), nullable=False),
        sa.Column("visited_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symptoms", sa.String(length=2000), nullable=False),
        sa.Column("recommendations", sa.String(length=2000), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("annulled_by", sa.BigInteger(), nullable=True),
        sa.Column("annulled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("annulment_reason", sa.String(length=500), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_visits"),
        sa.CheckConstraint(
            "status IN ('REGISTRADA', 'ANULADA')",
            name=conv("ck_visits_status_allowed"),
        ),
        sa.CheckConstraint(
            "(status = 'REGISTRADA' AND annulled_by IS NULL AND annulled_at IS NULL"
            " AND annulment_reason IS NULL) OR (status = 'ANULADA'"
            " AND annulled_by IS NOT NULL AND annulled_at IS NOT NULL"
            " AND annulment_reason IS NOT NULL"
            " AND btrim(annulment_reason) <> '')",
            name=conv("ck_visits_annulment_consistency"),
        ),
        sa.ForeignKeyConstraint(
            ["doctor_id"],
            ["doctors.id"],
            name="fk_visits_doctor_id_doctors",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name="fk_visits_patient_id_patients",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_visits_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["annulled_by"],
            ["users.id"],
            name="fk_visits_annulled_by_users",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_visits_created_by", "visits", ["created_by"], unique=False)
    op.create_index("ix_visits_annulled_by", "visits", ["annulled_by"], unique=False)
    op.create_index(
        "ix_visits_doctor_visited",
        "visits",
        ["doctor_id", sa.text("visited_at DESC"), sa.text("id DESC")],
        unique=False,
    )
    op.create_index(
        "ix_visits_patient_visited",
        "visits",
        ["patient_id", sa.text("visited_at DESC"), sa.text("id DESC")],
        unique=False,
    )
    op.create_index(
        "ix_visits_visited",
        "visits",
        [sa.text("visited_at DESC"), sa.text("id DESC")],
        unique=False,
    )
    op.create_index(
        "ix_visits_status_visited",
        "visits",
        ["status", sa.text("visited_at DESC"), sa.text("id DESC")],
        unique=False,
    )

    op.create_table(
        "visit_medications",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("visit_id", sa.BigInteger(), nullable=False),
        sa.Column("medication_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("instructions", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_visit_medications"),
        sa.UniqueConstraint("visit_id", "medication_id", name="uq_visit_medications"),
        sa.CheckConstraint(
            "quantity IS NULL OR quantity > 0",
            name=conv("ck_visit_medications_quantity_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["visit_id"],
            ["visits.id"],
            name="fk_visit_medications_visit_id_visits",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["medication_id"],
            ["medications.id"],
            name="fk_visit_medications_medication_id_medications",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_visit_medications_visit_id",
        "visit_medications",
        ["visit_id"],
        unique=False,
    )
    op.create_index(
        "ix_visit_medications_medication_id",
        "visit_medications",
        ["medication_id"],
        unique=False,
    )


def downgrade() -> None:
    """Elimina `visit_medications`, `visits` y `patients`."""
    op.drop_index("ix_visit_medications_medication_id", table_name="visit_medications")
    op.drop_index("ix_visit_medications_visit_id", table_name="visit_medications")
    op.drop_table("visit_medications")

    op.drop_index("ix_visits_status_visited", table_name="visits")
    op.drop_index("ix_visits_visited", table_name="visits")
    op.drop_index("ix_visits_patient_visited", table_name="visits")
    op.drop_index("ix_visits_doctor_visited", table_name="visits")
    op.drop_index("ix_visits_annulled_by", table_name="visits")
    op.drop_index("ix_visits_created_by", table_name="visits")
    op.drop_table("visits")

    op.drop_index("ix_patients_name_trgm", table_name="patients")
    op.drop_index("uq_patients_carnet", table_name="patients")
    op.drop_table("patients")
