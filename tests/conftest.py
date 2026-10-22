"""Fixtures compartidas de pytest."""

from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AsyncExitStack

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.rate_limit import get_login_rate_limiter
from app.db.session import get_session
from app.main import app
from tests.auth_support import cached_password_hash

DEFAULT_CLIENT = ("127.0.0.1", 1234)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Cliente HTTP que habla directo con la app ASGI (sin red)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest.fixture(scope="session")
async def db_engine() -> AsyncIterator[AsyncEngine]:
    """Motor compartido por toda la sesion (paso 2 de la optimizacion).

    Con `NullPool` no se retiene ninguna conexion entre tests: cada `db_conn`
    abre la suya y la cierra al terminar, asi que compartir el motor es seguro
    y evita crearlo y destruirlo 500+ veces.
    """
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _warmed_password_hashes() -> str:
    """Precalcula una vez el hash compartido (paso 1: ADMIN y MEDICO default).

    Ambos roles usan `DEFAULT_PASSWORD`, asi que un solo hash cubre a todos
    los actores que se autentican por token.
    """
    return cached_password_hash()


@pytest.fixture
async def db_conn(db_engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """Conexion con una transaccion que se revierte al terminar cada test.

    Los tests de integracion comparten la base de desarrollo (ya migrada con
    `alembic upgrade head`), pero nunca dejan datos: todo ocurre dentro de una
    transaccion que se deshace al finalizar.
    """
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            yield connection
        finally:
            await transaction.rollback()


@pytest.fixture
async def db_session(db_conn: AsyncConnection) -> AsyncIterator[AsyncSession]:
    """Sesion ORM unida a la transaccion de `db_conn` mediante savepoints.

    El `commit()` de los servicios libera un SAVEPOINT, nunca la transaccion
    externa, de modo que el `rollback()` de `db_conn` limpia todo al final.
    """
    session = AsyncSession(
        bind=db_conn,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        await session.close()


@pytest.fixture
async def auth_client_factory(
    db_session: AsyncSession,
) -> AsyncIterator[Callable[..., "AsyncClient"]]:
    """Fabrica clientes HTTP que usan `db_session` como dependencia de sesion.

    Permite construir clientes con otra IP (`build(ip="203.0.113.7")`) para los
    tests de rate limiting sin abrir conexiones reales.
    """
    app.dependency_overrides[get_session] = lambda: db_session
    stack = AsyncExitStack()

    async def build(ip: str = DEFAULT_CLIENT[0]) -> AsyncClient:
        transport = ASGITransport(app=app, client=(ip, DEFAULT_CLIENT[1]))
        return await stack.enter_async_context(
            AsyncClient(transport=transport, base_url="http://testserver")
        )

    try:
        yield build
    finally:
        app.dependency_overrides.pop(get_session, None)
        await stack.aclose()


@pytest.fixture
async def auth_client(
    auth_client_factory: Callable[..., "AsyncClient"],
) -> AsyncClient:
    """Cliente por defecto desde `127.0.0.1` con la sesion de prueba inyectada."""
    return await auth_client_factory()


@pytest.fixture(autouse=True)
def isolated_settings() -> Iterator[None]:
    """Evita que `get_settings()` conserve valores monkeypatcheados entre tests."""
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def isolated_rate_limiter() -> Iterator[None]:
    """Reinicia el rate limiter singleton antes y despues de cada test.

    La configuracion se restaura a los settings vigentes al inicio del test para
    que un `configure()` de otro test no se filtre a la suite.
    """
    settings = get_settings()
    default_max = settings.login_rate_limit_max_attempts
    default_window = settings.login_rate_limit_window_seconds

    limiter = get_login_rate_limiter()
    limiter.configure(max_attempts=default_max, window_seconds=default_window)
    yield
    get_login_rate_limiter().configure(max_attempts=default_max, window_seconds=default_window)
