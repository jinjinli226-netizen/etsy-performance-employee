from __future__ import annotations

from sqlalchemy import Engine

from app.db.base import Base
from app.db import models as _models  # noqa: F401
from app.db.migrations import run_sqlite_migrations


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    run_sqlite_migrations(engine)
