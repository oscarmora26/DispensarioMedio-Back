"""Modelo de medicos del dispensario."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Identity,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import UpdatedAtMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.visit import Visit


class Doctor(UpdatedAtMixin, Base):
    """Medico que registra visitas."""

    __tablename__ = "doctors"
    __table_args__ = (
        UniqueConstraint("cedula", name="uq_doctors_cedula"),
        CheckConstraint("cedula ~ '^[0-9]{11}$'", name="cedula_format"),
        Index(
            "ix_doctors_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    cedula: Mapped[str] = mapped_column(String(11), nullable=False)
    lab_shift: Mapped[str] = mapped_column(String(50), nullable=False)
    specialty: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)

    user: Mapped[User | None] = relationship(back_populates="doctor", uselist=False, lazy="raise")
    visits: Mapped[list[Visit]] = relationship(back_populates="doctor", lazy="raise")
