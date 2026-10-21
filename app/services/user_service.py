"""Servicios de provisionamiento y gestion de usuarios."""

from __future__ import annotations

import structlog
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.core.security import hash_password
from app.models.role import Role
from app.models.user import User
from app.repositories import doctor_repository, user_repository
from app.schemas.user import (
    SORT_FIELDS,
    UserCreate,
    UserListResponse,
    UserRead,
    UserUpdate,
)

logger = structlog.get_logger(__name__)

ADMIN_ROLE_NAME = "ADMIN"

USER_NOT_FOUND_MESSAGE = "El usuario no existe"
USER_EXISTS_MESSAGE = "El usuario ya existe"
DOCTOR_ASSIGNED_MESSAGE = "El médico ya está asignado a otro usuario"
INVALID_ROLE_MESSAGE = "El rol indicado no existe"
INVALID_DOCTOR_MESSAGE = "El médico indicado no existe"
ADMIN_DOCTOR_MESSAGE = "Un administrador no puede tener un médico asociado"
CANNOT_DEACTIVATE_SELF_MESSAGE = "No puede desactivar su propio usuario"
LAST_ADMIN_MESSAGE = "No se puede dejar el sistema sin un administrador activo"

ADMIN_ROLE_MISSING_MESSAGE = (
    "No existe el rol ADMIN. Ejecute 'alembic upgrade head' antes de crear el administrador"
)


class AdminRoleMissingError(AppError):
    """No existe el rol `ADMIN`; falta ejecutar las migraciones."""

    status_code = 500
    code = "admin_role_missing"


def parse_sort(sort: str) -> tuple[str, bool]:
    """Separa el campo de orden y el sentido descendente del parametro `sort`."""
    sort_desc = sort.startswith("-")
    sort_field = sort[1:] if sort_desc else sort
    if sort_field not in SORT_FIELDS:
        raise AppError(
            "Parámetro de orden inválido",
            status_code=422,
            code="validation_error",
            errors=[{"field": "sort", "message": "Valor de orden no permitido"}],
        )
    return sort_field, sort_desc


def calculate_pages(total: int, size: int) -> int:
    """Calcula el numero de paginas; `0` cuando no hay filas."""
    return (total + size - 1) // size if total else 0


def _to_read(user: User) -> UserRead:
    """Construye la forma publica del usuario a partir de la entidad ORM."""
    return UserRead(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role.name,
        doctor_id=user.doctor_id,
        is_active=user.is_active,
    )


def _touch(user: User) -> None:
    """Refresca `updated_at` con la hora de pared de la base.

    El modelo usa `onupdate=func.now()` (FEAT-003), pero en PostgreSQL `now()`
    es la hora de inicio de la transaccion: dentro de una transaccion larga el
    valor no cambiaria. `clock_timestamp()` mantiene la semantica esperada
    (cada UPDATE refresca la columna) sin tocar el esquema ni depender del reloj
    de la aplicacion.
    """
    user.updated_at = func.clock_timestamp()


def _integrity_constraint(exc: IntegrityError) -> str | None:
    """Extrae el nombre de la constraint violada de un `IntegrityError`."""
    constraint = getattr(exc.orig, "constraint_name", None)
    if isinstance(constraint, str) and constraint:
        return constraint

    haystack = f"{exc.orig} {exc}"
    for candidate in ("uq_users_username", "uq_users_email", "uq_users_doctor"):
        if candidate in haystack:
            return candidate
    return None


async def _commit(session: AsyncSession) -> None:
    """Confirma la transaccion traduciendo las violaciones de unicidad.

    Antes de responder se deshace el savepoint para que la sesion quede usable.
    """
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        constraint = _integrity_constraint(exc)
        if constraint in {"uq_users_username", "uq_users_email"}:
            raise ConflictError(USER_EXISTS_MESSAGE, code="user_exists") from None
        if constraint == "uq_users_doctor":
            raise ConflictError(DOCTOR_ASSIGNED_MESSAGE, code="doctor_already_assigned") from None
        raise


async def _get_user_or_404(
    session: AsyncSession,
    user_id: int,
    *,
    for_update: bool = False,
) -> User:
    """Carga un usuario o lanza `user_not_found`."""
    if for_update:
        user = await user_repository.get_user_by_id_for_update(session, user_id)
    else:
        user = await user_repository.get_user_by_id(session, user_id)
    if user is None:
        raise NotFoundError(USER_NOT_FOUND_MESSAGE, code="user_not_found")
    return user


async def _resolve_role(session: AsyncSession, role_id: int) -> Role:
    role = await user_repository.get_role_by_id(session, role_id)
    if role is None:
        raise AppError(INVALID_ROLE_MESSAGE, status_code=400, code="invalid_role")
    return role


async def _validate_doctor(session: AsyncSession, doctor_id: int | None, role: Role) -> None:
    """Valida existencia del medico y coherencia con el rol indicado."""
    if doctor_id is not None:
        doctor = await doctor_repository.get_doctor_by_id(session, doctor_id)
        if doctor is None:
            raise AppError(INVALID_DOCTOR_MESSAGE, status_code=400, code="invalid_doctor")
    if role.name == ADMIN_ROLE_NAME and doctor_id is not None:
        raise AppError(ADMIN_DOCTOR_MESSAGE, status_code=400, code="admin_cannot_have_doctor")


async def _ensure_unique(
    session: AsyncSession,
    *,
    username: str,
    email: str,
    doctor_id: int | None,
    exclude_id: int | None = None,
) -> None:
    """Comprueba duplicados de username/email y de medico."""
    existing = await user_repository.find_user_by_username_or_email(
        session,
        username=username,
        email=email,
        exclude_id=exclude_id,
    )
    if existing is not None:
        raise ConflictError(USER_EXISTS_MESSAGE, code="user_exists")

    if doctor_id is not None:
        assigned = await user_repository.find_user_by_doctor_id(
            session,
            doctor_id,
            exclude_id=exclude_id,
        )
        if assigned is not None:
            raise ConflictError(DOCTOR_ASSIGNED_MESSAGE, code="doctor_already_assigned")


async def _lock_admin_if_retired(session: AsyncSession, user: User, *, stays_admin: bool) -> None:
    """Bloquea el rol ADMIN y aplica la regla del ultimo administrador.

    Solo se ejecuta cuando la operacion retira un ADMIN activo; el orden de
    bloqueo es usuario (ya bloqueado) -> rol ADMIN -> conteo (FEAT-004 D-8).
    """
    if not user.is_active or user.role.name != ADMIN_ROLE_NAME or stays_admin:
        return

    admin_role_id = await user_repository.lock_admin_role(session)
    if admin_role_id is None:
        raise AdminRoleMissingError(ADMIN_ROLE_MISSING_MESSAGE)

    others = await user_repository.count_other_active_admins(session, user.id)
    if others == 0:
        raise AppError(LAST_ADMIN_MESSAGE, status_code=400, code="last_admin")


async def create_admin(
    session: AsyncSession,
    *,
    username: str,
    email: str,
    password: str,
) -> User:
    """Crea el administrador inicial. El llamante confirma la transaccion."""
    role = await user_repository.get_role_by_name(session, ADMIN_ROLE_NAME)
    if role is None:
        raise AdminRoleMissingError(ADMIN_ROLE_MISSING_MESSAGE)

    existing = await user_repository.find_user_by_username_or_email(
        session,
        username=username,
        email=email,
    )
    if existing is not None:
        raise ConflictError(USER_EXISTS_MESSAGE, code="user_exists")

    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        role_id=role.id,
        role=role,
        doctor_id=None,
        is_active=True,
        last_login_at=None,
    )
    session.add(user)
    await session.flush()
    return user


async def list_users(
    session: AsyncSession,
    *,
    page: int,
    size: int,
    search: str | None = None,
    is_active: bool | None = None,
    sort: str = "username",
) -> UserListResponse:
    """Lista usuarios con paginacion, busqueda, filtro de estado y orden."""
    sort_field, sort_desc = parse_sort(sort)
    normalized_search = search.strip() if search else None
    users, total = await user_repository.list_users(
        session,
        page=page,
        size=size,
        search=normalized_search or None,
        is_active=is_active,
        sort_field=sort_field,
        sort_desc=sort_desc,
    )
    pages = calculate_pages(total, size)
    return UserListResponse(
        items=[_to_read(user) for user in users],
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


async def get_user(session: AsyncSession, *, user_id: int) -> UserRead:
    """Devuelve un usuario por identificador, incluido si esta inactivo."""
    user = await _get_user_or_404(session, user_id)
    return _to_read(user)


async def create_user(
    session: AsyncSession,
    *,
    actor: User,
    payload: UserCreate,
) -> UserRead:
    """Crea un usuario y devuelve su forma publica."""
    role = await _resolve_role(session, payload.role_id)
    await _validate_doctor(session, payload.doctor_id, role)
    await _ensure_unique(
        session,
        username=payload.username,
        email=payload.email,
        doctor_id=payload.doctor_id,
    )

    user = User(
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role_id=role.id,
        role=role,
        doctor_id=payload.doctor_id,
        is_active=payload.is_active,
        last_login_at=None,
    )
    session.add(user)
    await _commit(session)

    logger.info("user.created", user_id=user.id, actor_id=actor.id)
    return _to_read(user)


async def update_user(
    session: AsyncSession,
    *,
    actor: User,
    user_id: int,
    payload: UserUpdate,
) -> UserRead:
    """Reemplaza los campos editables de un usuario (FEAT-004 D-1)."""
    user = await _get_user_or_404(session, user_id, for_update=True)

    role = await _resolve_role(session, payload.role_id)
    await _validate_doctor(session, payload.doctor_id, role)

    if not payload.is_active and user.id == actor.id:
        raise AppError(
            CANNOT_DEACTIVATE_SELF_MESSAGE, status_code=400, code="cannot_deactivate_self"
        )

    await _lock_admin_if_retired(
        session,
        user,
        stays_admin=payload.is_active and role.name == ADMIN_ROLE_NAME,
    )

    await _ensure_unique(
        session,
        username=payload.username,
        email=payload.email,
        doctor_id=payload.doctor_id,
        exclude_id=user.id,
    )

    was_active = user.is_active
    user.username = payload.username
    user.email = payload.email
    user.role_id = role.id
    user.role = role
    user.doctor_id = payload.doctor_id
    user.is_active = payload.is_active
    _touch(user)
    await _commit(session)

    event = "user.deactivated" if was_active and not user.is_active else "user.updated"
    logger.info(event, user_id=user.id, actor_id=actor.id)
    return _to_read(user)


async def deactivate_user(
    session: AsyncSession,
    *,
    actor: User,
    user_id: int,
) -> None:
    """Desactiva logicamente un usuario; es idempotente si ya estaba inactivo."""
    user = await _get_user_or_404(session, user_id, for_update=True)

    if user.id == actor.id:
        raise AppError(
            CANNOT_DEACTIVATE_SELF_MESSAGE, status_code=400, code="cannot_deactivate_self"
        )

    if not user.is_active:
        return

    await _lock_admin_if_retired(session, user, stays_admin=False)

    user.is_active = False
    _touch(user)
    await _commit(session)
    logger.info("user.deactivated", user_id=user.id, actor_id=actor.id)


async def reset_password(
    session: AsyncSession,
    *,
    actor: User,
    user_id: int,
    new_password: str,
) -> None:
    """Reemplaza el hash de la contrasena sin cambiar el estado de la cuenta."""
    user = await _get_user_or_404(session, user_id)

    user.password_hash = hash_password(new_password)
    _touch(user)
    await _commit(session)
    logger.info("user.password_reset", user_id=user.id, actor_id=actor.id)
