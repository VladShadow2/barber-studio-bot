import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from booking_bot.db import models  # noqa: F401
from booking_bot.db.base import Base
from booking_bot.db.session import dispose_engine, init_engine


def _test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    name = url.rsplit("/", 1)[-1].split("?", 1)[0]
    if "test" not in name.lower():
        pytest.skip("TEST_DATABASE_URL database name must contain 'test'")
    return url


@pytest.fixture
async def postgres():
    """Recreate the schema in the disposable test database."""

    url = _test_database_url()
    engine = create_async_engine(url, connect_args={"statement_cache_size": 0})
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await connection.execute(text("CREATE SCHEMA public"))
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        await connection.run_sync(Base.metadata.create_all)
    factory = init_engine(url)
    yield factory
    await dispose_engine()
    await engine.dispose()
