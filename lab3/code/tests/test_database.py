"""真实文件数据库的初始化、版本保护和事务边界。"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.errors import ConflictError, NotFoundError, SchemaVersionError
from app.models import Ledger, Membership, Role, User
from app.storage import schema
from app.storage.database import Database
from app.storage.store import LedgerStore


def test_initialization_is_idempotent_and_preserves_rows(tmp_path):
    database = Database(tmp_path / "nested" / "ledger.sqlite3")
    store = LedgerStore(database)
    user = User(name="保留用户")
    with store.transaction() as repositories:
        repositories.users.add(user)
    assert database.initialize() == schema.SCHEMA_VERSION
    with store.transaction(write=False) as repositories:
        assert repositories.users.get(user.id) == user
    with database.connect(write=False) as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        assert schema.TABLES <= names
        assert connection.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_concurrent_startup_creates_schema_once(tmp_path):
    path = tmp_path / "simultaneous.sqlite3"
    with ThreadPoolExecutor(max_workers=4) as executor:
        versions = list(executor.map(lambda _: Database(path).initialize(), range(4)))
    assert versions == [schema.SCHEMA_VERSION] * 4


def test_failed_migration_rolls_back_ddl_and_version(tmp_path, monkeypatch):
    database = Database(tmp_path / "migration.sqlite3")
    monkeypatch.setattr(schema, "STATEMENTS", (
        "CREATE TABLE partial_table(id INTEGER)", "THIS IS NOT SQL",
    ))
    with pytest.raises(sqlite3.OperationalError):
        database.initialize()
    with database.connect(write=False) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []


def test_future_database_is_never_overwritten(tmp_path):
    database = Database(tmp_path / "future.sqlite3")
    with database.connect() as connection:
        connection.execute("CREATE TABLE future_data(value TEXT)")
        connection.execute("INSERT INTO future_data VALUES ('keep me')")
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(SchemaVersionError):
        database.initialize()
    with database.connect(write=False) as connection:
        assert connection.execute("SELECT value FROM future_data").fetchone()[0] == "keep me"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99


def test_unknown_or_incomplete_database_is_not_silently_rebuilt(tmp_path):
    unknown = Database(tmp_path / "unknown.sqlite3")
    with unknown.connect() as connection:
        connection.execute("CREATE TABLE unknown_table(id INTEGER)")
    with pytest.raises(SchemaVersionError):
        unknown.initialize()
    incomplete = Database(tmp_path / "incomplete.sqlite3")
    with incomplete.connect() as connection:
        connection.execute(f"PRAGMA user_version = {schema.SCHEMA_VERSION}")
    with pytest.raises(SchemaVersionError, match="不完整"):
        incomplete.initialize()


def test_read_only_transaction_cannot_write(book):
    user = User(name="不应写入")
    with pytest.raises(sqlite3.OperationalError):
        with book.store.transaction(write=False) as repositories:
            repositories.users.add(user)
    with book.store.transaction(write=False) as repositories:
        with pytest.raises(NotFoundError):
            repositories.users.get(user.id)


def test_ledger_creation_failure_is_atomic_even_when_caught(book):
    ledger = Ledger(name="不能留下一半")
    owner = Membership(user_id=uuid4(), ledger_id=ledger.id, role=Role.OWNER)
    with book.store.transaction() as repositories:
        with pytest.raises(ConflictError):
            repositories.ledgers.create(ledger, owner)
        with pytest.raises(NotFoundError):
            repositories.ledgers.get(ledger.id)
        repositories.users.add(User(name="外层事务仍可继续"))
    with book.store.transaction(write=False) as repositories:
        assert repositories.ledgers.get(book.ledger.id) == book.ledger
