"""Endpoints de celdas (cells)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.db.session import get_session
from app.models.user import User
from app.schemas.shelf import CellCreate, CellListResponse, CellRead, CellUpdate
from app.services import cell_service

router = APIRouter(prefix="/cells", tags=["cells"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ReaderUser = Annotated[User, Depends(require_role("ADMIN", "MEDICO"))]
WriterUser = Annotated[User, Depends(require_role("ADMIN"))]

SORT_PATTERN = r"^-?(?:section|cell|shelf_id|created_at|is_active|id)$"


@router.get(
    "",
    response_model=CellListResponse,
    summary="Listar celdas",
    description=(
        "Lista celdas con paginacion, busqueda case-insensitive e insensible a "
        "acentos sobre section, cell y description, filtros opcionales por "
        "estado y estante, y orden por el catalogo permitido. "
        "Accesible para ADMIN y MEDICO."
    ),
)
async def list_cells(
    session: SessionDep,
    _actor: ReaderUser,
    page: Annotated[int, Query(ge=1, description="Pagina, desde 1")] = 1,
    size: Annotated[int, Query(ge=1, le=100, description="Elementos por pagina")] = 20,
    search: Annotated[
        str | None,
        Query(max_length=100, description="Busqueda parcial en section, cell y description"),
    ] = None,
    is_active: Annotated[bool | None, Query(description="Filtra por estado")] = None,
    shelf_id: Annotated[int | None, Query(ge=1, description="Filtra por estante")] = None,
    sort: Annotated[
        str,
        Query(
            pattern=SORT_PATTERN,
            description=(
                "section, cell, shelf_id, created_at, is_active o id; prefijo '-' descendente"
            ),
        ),
    ] = "id",
) -> CellListResponse:
    """Lista celdas con paginacion y filtros."""
    return await cell_service.list_cells(
        session,
        page=page,
        size=size,
        search=search,
        is_active=is_active,
        shelf_id=shelf_id,
        sort=sort,
    )


@router.get(
    "/{cell_id}",
    response_model=CellRead,
    summary="Detalle de celda",
    description="Devuelve una celda. Accesible para ADMIN y MEDICO.",
)
async def get_cell(cell_id: int, session: SessionDep, _actor: ReaderUser) -> CellRead:
    """Devuelve una celda por identificador."""
    return await cell_service.get_cell(session, cell_id)


@router.post(
    "",
    response_model=CellRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear celda",
    description=(
        "Crea una celda en un estante existente. La posicion (shelf_id, section, "
        "cell) es unica sin distinguir mayusculas. Solo accesible para ADMIN."
    ),
    responses={status.HTTP_409_CONFLICT: {"description": "La posicion ya existe"}},
)
async def create_cell(payload: CellCreate, session: SessionDep, actor: WriterUser) -> CellRead:
    """Crea una celda."""
    cell = await cell_service.create_cell(session, payload, actor=actor)
    await session.commit()
    return cell


@router.put(
    "/{cell_id}",
    response_model=CellRead,
    summary="Reemplazar celda",
    description=(
        "Reemplazo completo de shelf_id, section, cell, description e is_active; "
        "permite mover la celda de estante. Solo accesible para ADMIN."
    ),
    responses={status.HTTP_409_CONFLICT: {"description": "La posicion ya existe"}},
)
async def update_cell(
    cell_id: int, payload: CellUpdate, session: SessionDep, actor: WriterUser
) -> CellRead:
    """Reemplaza una celda."""
    cell = await cell_service.update_cell(session, cell_id, payload, actor=actor)
    await session.commit()
    return cell


@router.delete(
    "/{cell_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Desactivar celda",
    description=(
        "Baja logica de la celda. Responde 409 si la celda tiene medicamentos "
        "asociados. Solo accesible para ADMIN."
    ),
    responses={status.HTTP_409_CONFLICT: {"description": "La celda tiene medicamentos"}},
)
async def delete_cell(cell_id: int, session: SessionDep, actor: WriterUser) -> None:
    """Desactiva la celda."""
    await cell_service.delete_cell(session, cell_id, actor=actor)
    await session.commit()
