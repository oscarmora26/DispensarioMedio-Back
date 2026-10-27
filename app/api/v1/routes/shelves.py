"""Endpoints de estantes (shelves)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.db.session import get_session
from app.models.user import User
from app.schemas.shelf import (
    ShelfCreate,
    ShelfListResponse,
    ShelfRead,
    ShelfUpdate,
)
from app.services import shelf_service

router = APIRouter(prefix="/shelves", tags=["shelves"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ReaderUser = Annotated[User, Depends(require_role("ADMIN", "MEDICO"))]
WriterUser = Annotated[User, Depends(require_role("ADMIN"))]

SORT_PATTERN = r"^-?(?:name|created_at|is_active|id)$"


@router.get(
    "",
    response_model=ShelfListResponse,
    summary="Listar estantes",
    description=(
        "Lista estantes con sus celdas anidadas, con paginacion, busqueda "
        "case-insensitive e insensible a acentos sobre name y description, "
        "filtro opcional por estado y orden por el catalogo permitido. "
        "Accesible para ADMIN y MEDICO."
    ),
)
async def list_shelves(
    session: SessionDep,
    _actor: ReaderUser,
    page: Annotated[int, Query(ge=1, description="Pagina, desde 1")] = 1,
    size: Annotated[int, Query(ge=1, le=100, description="Elementos por pagina")] = 20,
    search: Annotated[
        str | None,
        Query(max_length=100, description="Busqueda parcial en name y description"),
    ] = None,
    is_active: Annotated[bool | None, Query(description="Filtra por estado")] = None,
    sort: Annotated[
        str,
        Query(
            pattern=SORT_PATTERN,
            description="name, created_at, is_active o id; prefijo '-' descendente",
        ),
    ] = "name",
) -> ShelfListResponse:
    """Lista estantes con sus celdas anidadas."""
    return await shelf_service.list_shelves(
        session, page=page, size=size, search=search, is_active=is_active, sort=sort
    )


@router.get(
    "/{shelf_id}",
    response_model=ShelfRead,
    summary="Detalle de estante",
    description="Devuelve un estante con sus celdas. Accesible para ADMIN y MEDICO.",
)
async def get_shelf(shelf_id: int, session: SessionDep, _actor: ReaderUser) -> ShelfRead:
    """Devuelve un estante con sus celdas."""
    return await shelf_service.get_shelf(session, shelf_id)


@router.post(
    "",
    response_model=ShelfRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear estante",
    description=(
        "Crea un estante con name unico (case-insensitive). Las celdas se crean "
        "por POST /api/v1/cells. Solo accesible para ADMIN."
    ),
)
async def create_shelf(payload: ShelfCreate, session: SessionDep, actor: WriterUser) -> ShelfRead:
    """Crea un estante sin celdas."""
    shelf = await shelf_service.create_shelf(session, payload, actor=actor)
    await session.commit()
    return shelf


@router.put(
    "/{shelf_id}",
    response_model=ShelfRead,
    summary="Reemplazar estante",
    description=(
        "Reemplazo completo de name, description e is_active. No modifica las "
        "celdas: esas se gestionan por /api/v1/cells. Solo accesible para ADMIN."
    ),
)
async def update_shelf(
    shelf_id: int, payload: ShelfUpdate, session: SessionDep, actor: WriterUser
) -> ShelfRead:
    """Reemplaza los campos del estante."""
    shelf = await shelf_service.update_shelf(session, shelf_id, payload, actor=actor)
    await session.commit()
    return shelf


@router.delete(
    "/{shelf_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Desactivar estante",
    description=(
        "Baja logica: desactiva el estante y todas sus celdas. Los registros no "
        "se borran. Solo accesible para ADMIN."
    ),
)
async def delete_shelf(shelf_id: int, session: SessionDep, actor: WriterUser) -> None:
    """Desactiva el estante y sus celdas."""
    await shelf_service.delete_shelf(session, shelf_id, actor=actor)
    await session.commit()
