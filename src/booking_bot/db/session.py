from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(url: str) -> async_sessionmaker[AsyncSession]:
    """Bind the process to a PostgreSQL URL. Safe to call again in tests."""

    global _engine, _sessionmaker
    _engine = create_async_engine(
        url,
        pool_pre_ping=True,
        connect_args={"statement_cache_size": 0},
    )
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _sessionmaker


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        from booking_bot.config import load_settings

        return init_engine(load_settings().database_url)
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
