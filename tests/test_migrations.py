"""The Alembic migrations must build exactly the schema the models describe."""

import os

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from app.db import Base
from app.migrate import upgrade


@pytest.mark.skipif(bool(os.environ.get("TEST_DATABASE_URL")), reason="SQLite file check only")
def test_migrations_match_models(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    upgrade(url)
    engine = create_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []
