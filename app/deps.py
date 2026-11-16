"""Shared FastAPI dependencies."""
from collections.abc import Iterator
from .db import DB


def get_db() -> Iterator[DB]:
    """One SQLite connection per request, always closed afterwards."""
    db = DB()
    try:
        yield db
    finally:
        db.close()
