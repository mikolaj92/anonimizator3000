"""Shared SQLite auth database location and lifecycle helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Final

from my_auth import inspect_sqlite_schema as inspect_my_auth_schema
from my_usermanager.adapters.my_auth_sqlite import SQLiteAuthDatabase
from my_usermanager.adapters.sqlite import inspect_sqlite_schema as inspect_um_schema

from anonimizator3000.config import Settings, settings_from_env

_PACKAGE_DIR: Final[Path] = Path(__file__).resolve().parent
_DEFAULT_AUTH_DB: Final[Path] = _PACKAGE_DIR.parents[1] / "storage" / "auth.sqlite3"


def auth_db_path(settings: Settings | None = None) -> Path:
    """Resolve the durable SQLite path for my-auth + my-usermanager tables."""
    configured = (settings or settings_from_env()).auth_db_path
    path = Path(configured).expanduser() if configured else _DEFAULT_AUTH_DB
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_auth_database(settings: Settings | None = None) -> SQLiteAuthDatabase:
    """Return the canonical shared auth database coordinator."""
    return SQLiteAuthDatabase(auth_db_path(settings))


def inspect_auth_schema(database: str | Path | sqlite3.Connection) -> tuple[object, str]:
    """Inspect both auth schemas without creating or migrating anything."""
    if isinstance(database, sqlite3.Connection):
        return inspect_my_auth_schema(database), inspect_um_schema(database)
    connection = sqlite3.connect(database)
    try:
        return inspect_my_auth_schema(connection), inspect_um_schema(connection)
    finally:
        connection.close()


def migrate_auth_database(settings: Settings | None = None) -> SQLiteAuthDatabase:
    """Initialize or migrate shared auth schemas and return the coordinator."""
    database = get_auth_database(settings)
    database.initialize()
    return database
