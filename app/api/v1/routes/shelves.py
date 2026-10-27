"""Endpoints de estantes (shelves) y celdas (cells)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.core.exceptions import AppError
from app.db.session import get_session
from app.models.user import User
from app.schemas.shelf import ShelfCreate, ShelfListResponse, ShelfRead, ShelfUpdate
from app.services import shelf_service
from app.services.shelf_service import calculate_pages

ReaderUser = Annotated[User, Depends(require_role("ADMIN", "MEDICO"))]
WriterUser = Annotated[User, Depends(require_role("ADMIN"))]

router = APIRouter(prefix="/shelves", tags=["shelves"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _handle(exc: AppError):
    raise exc


@router.get("", response_model=ShelfListResponse)
async def list_shelves(
    session: SessionDep,
    _actor: ReaderUser,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    search: Annotated[str | None, Query(max_length=100)] = None,
    is_active: Annotated[bool | None, Query()] = None,
    sort: Annotated[str, Query()] = "name",
) -> ShelfListResponse:
    shelves, total = await shelf_service.list_shelves(
        session, page=page, size=size, search=search, is_active=is_active, sort=sort
    )
    pages = calculate_pages(total, size)
    return ShelfListResponse(
        items=[ShelfRead.model_validate(s) for s in shelves],
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


@router.get("/{shelf_id}", response_model=ShelfRead)
async def get_shelf(shelf_id: int, session: SessionDep, _actor: ReaderUser) -> ShelfRead:
    shelf = await shelf_service.get_shelf(session, shelf_id)
    return ShelfRead.model_validate(shelf)


@router.post("", response_model=ShelfRead, status_code=status.HTTP_201_CREATED)
async def create_shelf(payload: ShelfCreate, session: SessionDep, _actor: WriterUser) -> ShelfRead:
    shelf = await shelf_service.create_shelf(session, payload)
    await session.commit()
    return ShelfRead.model_validate(shelf)


@router.put("/{shelf_id}", response_model=ShelfRead)
async def update_shelf(
    shelf_id: int, payload: ShelfUpdate, session: SessionDep, _actor: WriterUser
) -> ShelfRead:
    shelf = await shelf_service.update_shelf(session, shelf_id, payload)
    await session.commit()
    return ShelfRead.model_validate(shelf)


@router.delete("/{shelf_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shelf(shelf_id: int, session: SessionDep, _actor: WriterUser) -> None:
    await shelf_service.delete_shelf(session, shelf_id)
    await session.commit()
