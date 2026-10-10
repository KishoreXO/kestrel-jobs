from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, inspect, text
from sqlmodel import Session, SQLModel, create_engine

from kestrel import models  # noqa: F401  (registers tables)
from kestrel.config import get_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    engine = create_engine(f"sqlite:///{settings.db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    add_missing_columns(engine)
    return engine


def add_missing_columns(engine: Engine) -> None:
    """create_all never alters existing tables, so add columns introduced by newer versions."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl = col.type.compile(dialect=engine.dialect)
                default = (
                    getattr(col.default, "arg", None) if col.default is not None and col.default.is_scalar else None
                )
                clause = (
                    f" DEFAULT {default!r}"
                    if isinstance(default, str)
                    else (f" DEFAULT {int(default)}" if isinstance(default, (bool, int)) else "")
                )
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}{clause}'))


@contextmanager
def session_scope() -> Iterator[Session]:
    with Session(get_engine(), expire_on_commit=False) as session:
        yield session


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with Session(get_engine(), expire_on_commit=False) as session:
        yield session
