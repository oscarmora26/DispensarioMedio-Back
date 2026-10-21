"""Modelo de auditoria de acciones sobre el sistema."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Identity, String
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import CreatedAtMixin

if TYPE_CHECKING:
    from app.models.user import User


class INETAsString(INET):
    """`INET` que materializa los valores como `str`, sea cual sea el engine.

    El flag `native_inet_types=False` vive en el dialecto asyncpg (ver
    `app/db/session.py`), pero cualquier engine que no lo declare devolveria
    `ipaddress.IPv4Address`. Este procesador mantiene el contrato `str | None`
    del modelo y no altera el tipo fisico (`INET`).
    """

    def result_processor(
        self, dialect: Dialect, coltype: object
    ) -> Callable[[Any], str | None] | None:
        def process(value: Any) -> str | None:
            return None if value is None else str(value)

        return process


class AuditLog(CreatedAtMixin, Base):
    """Registro de auditoria; v1 solo define la tabla, sin escritura."""

    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint(
            "action IN ('CREATE', 'UPDATE', 'DEACTIVATE', 'ANNUL', 'LOGIN')",
            name="action_allowed",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True, nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    entity: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    old_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    new_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ip: Mapped[str | None] = mapped_column(INETAsString(), nullable=True)

    user: Mapped[User | None] = relationship(lazy="raise")
