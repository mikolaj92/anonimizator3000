from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from my_auth.sqlite_schema import ensure_sqlite_schema as ensure_auth_schema
from my_usermanager.adapters.sqlite import create_tables as create_um_tables

from anonimizator3000.auth_stores import inspect_auth_schema, migrate_auth_database
from anonimizator3000.config import Settings


def _legacy_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        ensure_auth_schema(connection)
        create_um_tables(connection)
        connection.execute("ALTER TABLE passkey_challenges RENAME TO challenges_v3")
        connection.execute(
            """CREATE TABLE passkey_challenges (
            key TEXT NOT NULL, kind TEXT NOT NULL, challenge BLOB NOT NULL,
            expires_at TEXT NOT NULL, user_id TEXT, user_handle TEXT,
            user_name TEXT, user_display_name TEXT, PRIMARY KEY (key, kind)
            )"""
        )
        connection.execute(
            """INSERT INTO passkey_challenges
            (key, kind, challenge, expires_at, user_id, user_handle, user_name, user_display_name)
            SELECT key, kind, challenge, expires_at, user_id, user_handle,
            user_name, user_display_name
            FROM challenges_v3"""
        )
        connection.execute("DROP TABLE challenges_v3")
        connection.execute(
            "CREATE INDEX idx_passkey_challenges_expires_at "
            "ON passkey_challenges(expires_at)"
        )
        connection.execute("UPDATE my_auth_schema SET schema_version = 2")
        connection.execute("ALTER TABLE um_users RENAME TO um_users_v5")
        connection.execute(
            """CREATE TABLE um_users (
            user_id TEXT PRIMARY KEY, username TEXT NOT NULL,
            first_name TEXT, last_name TEXT, display_name TEXT, email TEXT,
            birth_date TEXT, gender TEXT, disabled INTEGER NOT NULL DEFAULT 0,
            system INTEGER NOT NULL DEFAULT 0, scope_type TEXT, scope_id TEXT
            )"""
        )
        connection.execute(
            """INSERT INTO um_users
            (user_id, username, first_name, last_name, display_name, email,
             birth_date, gender, disabled, system, scope_type, scope_id)
            SELECT user_id, username, first_name, last_name, display_name, email,
             birth_date, gender, disabled, system, scope_type, scope_id
            FROM um_users_v5"""
        )
        connection.execute("DROP TABLE um_users_v5")
        connection.execute("ALTER TABLE um_external_identities RENAME TO identities_old")
        connection.execute("ALTER TABLE um_grants RENAME TO grants_old")
        connection.execute(
            """CREATE TABLE um_external_identities (
            provider TEXT NOT NULL, subject TEXT NOT NULL,
            user_id TEXT NOT NULL REFERENCES um_users(user_id) ON DELETE CASCADE,
            PRIMARY KEY (provider, subject))"""
        )
        connection.execute(
            "INSERT INTO um_external_identities SELECT * FROM identities_old"
        )
        connection.execute(
            """CREATE TABLE um_grants (
            user_id TEXT NOT NULL REFERENCES um_users(user_id) ON DELETE CASCADE,
            role_name TEXT NOT NULL DEFAULT '',
            permission_name TEXT NOT NULL DEFAULT '',
            scope_type TEXT NOT NULL DEFAULT '', scope_id TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (user_id, role_name, permission_name, scope_type, scope_id),
            CHECK ((role_name = '') != (permission_name = '')))"""
        )
        connection.execute("INSERT INTO um_grants SELECT * FROM grants_old")
        connection.execute("DROP TABLE identities_old")
        connection.execute("DROP TABLE grants_old")
        connection.execute("UPDATE um_schema_version SET version = 3")
        connection.commit()
    finally:
        connection.close()


def test_legacy_database_migrates_with_consistent_backup(tmp_path: Path) -> None:
    database = tmp_path / "auth.sqlite3"
    _legacy_database(database)
    settings = Settings(auth_db_path=str(database))

    migrate_auth_database(settings)

    auth_state, um_state = inspect_auth_schema(database)
    assert auth_state.state == "current"
    assert um_state == "current"
    backups = list(tmp_path.glob("auth.sqlite3.pre-migration-*.sqlite3"))
    assert len(backups) == 1
    backup_auth, backup_um = inspect_auth_schema(backups[0])
    assert backup_auth.state == "legacy"
    assert backup_um == "v3"


def test_unknown_schema_is_backed_up_then_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "auth.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE my_auth_schema (schema_version INTEGER NOT NULL)")
    connection.execute("INSERT INTO my_auth_schema VALUES (999)")
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="unsupported"):
        migrate_auth_database(Settings(auth_db_path=str(database)))

    assert len(list(tmp_path.glob("auth.sqlite3.pre-migration-*.sqlite3"))) == 1
    connection = sqlite3.connect(database)
    try:
        assert connection.execute(
            "SELECT schema_version FROM my_auth_schema"
        ).fetchone() == (999,)
    finally:
        connection.close()
