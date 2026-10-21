"""Fixtures compartidas de pytest."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.main import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Cliente HTTP que habla directo con la app ASGI (sin red)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest.fixture
async def db_conn() -> AsyncIterator[AsyncConnection]:
    """Conexion con una transaccion que se revierte al terminar cada test.

    Los tests de integracion comparten la base de desarrollo (ya migrada con
    `alembic upgrade head`), pero nunca dejan datos: todo ocurre dentro de una
    transaccion que se deshace al finalizar.
    """
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                yield connection
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
