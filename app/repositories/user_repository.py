"""Acceso a datos de usuarios y roles."""

from __future__ import annotations

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.role import Role
from app.models.user import User

# Columna de ordenacion permitida para cada campo del catalogo de FEAT-004 D-3.
SORT_COLUMNS = {
    "username": User.username,
    "email": User.email,
    "created_at": User.created_at,
    "last_login_at": User.last_login_at,
    "id": User.id,
}


def escape_like(value: str) -> str:
    """Escapa `\\`, `%` y `_` para que `ILIKE` los trate como literales."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    """Busca un usuario por `username` (CITEXT: case-insensitive)."""
    result = await session.execute(
        select(User).options(selectinload(User.role)).where(User.username == username)
    )
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: int) -> User | None:
    """Busca un usuario por identificador con su rol cargado."""
    result = await session.execute(
        select(User).options(selectinload(User.role)).where(User.id == user_id)
    )
    return result.scalar_one_or_none()


async def get_user_by_id_for_update(session: AsyncSession, user_id: int) -> User | None:
    """Bloquea la fila del usuario hasta el final de la transaccion."""
    result = await session.execute(
        select(User).options(selectinload(User.role)).where(User.id == user_id).with_for_update()
    )
    return result.scalar_one_or_none()


async def get_role_by_name(session: AsyncSession, name: str) -> Role | None:
    """Busca un rol por su nombre exacto."""
    result = await session.execute(select(Role).where(Role.name == name))
    return result.scalar_one_or_none()


async def get_role_by_id(session: AsyncSession, role_id: int) -> Role | None:
    """Busca un rol por identificador."""
    result = await session.execute(select(Role).where(Role.id == role_id))
    return result.scalar_one_or_none()


async def lock_admin_role(session: AsyncSession) -> int | None:
    """Bloquea la fila del rol `ADMIN` y devuelve su id, o `None` si no existe.

    El servicio decide que excepcion lanzar cuando la semilla de roles falta.
    """
    result = await session.execute(select(Role.id).where(Role.name == "ADMIN").with_for_update())
    return result.scalar_one_or_none()


async def count_other_active_admins(session: AsyncSession, user_id: int) -> int:
    """Cuenta administradores activos distintos del usuario indicado."""
    result = await session.execute(
        select(func.count(User.id))
        .join(Role, Role.id == User.role_id)
        .where(Role.name == "ADMIN", User.is_active.is_(True), User.id != user_id)
    )
    return int(result.scalar_one())


def _list_conditions(*, search: str | None, is_active: bool | None) -> list[object]:
    conditions: list[object] = []
    if search:
        pattern = f"%{escape_like(search)}%"
        conditions.append(
            or_(
                User.username.ilike(pattern, escape="\\"),
                User.email.ilike(pattern, escape="\\"),
            )
        )
    if is_active is not None:
        conditions.append(User.is_active.is_(is_active))
    return conditions


def _ordered(
    statement: Select[tuple[User]], *, sort_field: str, sort_desc: bool
) -> Select[tuple[User]]:
    column = SORT_COLUMNS[sort_field]
    if sort_field == "last_login_at":
        ordering = column.desc().nulls_last() if sort_desc else column.asc().nulls_last()
    else:
        ordering = column.desc() if sort_desc else column.asc()

    order_by = [ordering]
    if sort_field != "id":
        order_by.append(User.id.asc())
    return statement.order_by(*order_by)


async def list_users(
    session: AsyncSession,
    *,
    page: int,
    size: int,
    search: str | None,
    is_active: bool | None,
    sort_field: str,
    sort_desc: bool,
) -> tuple[list[User], int]:
    """Devuelve una pagina de usuarios y el total de filas que cumplen los filtros."""
    conditions = _list_conditions(search=search, is_active=is_active)

    total_result = await session.execute(select(func.count(User.id)).where(*conditions))
    total = int(total_result.scalar_one())

    statement = select(User).options(selectinload(User.role)).where(*conditions)
    statement = _ordered(statement, sort_field=sort_field, sort_desc=sort_desc)
    result = await session.execute(statement.limit(size).offset((page - 1) * size))
    return list(result.scalars().all()), total


async def find_user_by_username_or_email(
    session: AsyncSession,
    *,
    username: str,
    email: str,
    exclude_id: int | None = None,
) -> User | None:
    """Busca un usuario cuyo `username` o `email` coincida (ambos CITEXT).

    `limit(1)` evita `MultipleResultsFound` cuando el `username` coincide con un
    usuario y el `email` con otro distinto: para detectar el duplicado basta con
    que exista una fila. `exclude_id` permite ignorar al propio usuario en un
    reemplazo completo.
    """
    conditions = [or_(User.username == username, User.email == email)]
    if exclude_id is not None:
        conditions.append(User.id != exclude_id)
    result = await session.execute(select(User).where(*conditions).limit(1))
    return result.scalar_one_or_none()


async def find_user_by_doctor_id(
    session: AsyncSession,
    doctor_id: int,
    *,
    exclude_id: int | None = None,
) -> int | None:
    """Devuelve el id del usuario que ya tiene asignado el medico, si existe."""
    conditions = [User.doctor_id == doctor_id]
    if exclude_id is not None:
        conditions.append(User.id != exclude_id)
    result = await session.execute(select(User.id).where(*conditions).limit(1))
    return result.scalar_one_or_none()
