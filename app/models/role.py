"""Modelo de roles del sistema."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, Identity, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import CreatedAtMixin

if TYPE_CHECKING:
    from app.models.user import User


class Role(CreatedAtMixin, Base):
    """Rol asignado a un usuario (`ADMIN` o `MEDICO`)."""

    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("name", name="uq_roles_name"),
        CheckConstraint("name IN ('ADMIN', 'MEDICO')", name="name_allowed"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(String(100), nullable=True)

    users: Mapped[list[User]] = relationship(back_populates="role", lazy="raise")
