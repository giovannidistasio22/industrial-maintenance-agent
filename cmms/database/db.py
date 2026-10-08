"""
CMMS - Connessione al database (SQLAlchemy 2.0)

DATABASE_URL di default punta al PostgreSQL di docker-compose.yml.
Può essere sovrascritta via variabile d'ambiente (es. SQLite nei test).
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import StaticPool

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://cmms:cmms@localhost:5432/cmms")

_engine_kwargs: dict = {}
if DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
    if ":memory:" in DATABASE_URL:
        _engine_kwargs["poolclass"] = StaticPool
else:
    _engine_kwargs["pool_pre_ping"] = True  # scarta connessioni cadute (es. DB riavviato)

engine = create_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """Dependency FastAPI: una sessione DB per richiesta, chiusa a fine richiesta."""
    with SessionLocal() as db:
        yield db
