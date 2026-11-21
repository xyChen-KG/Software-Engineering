"""已有版本一数据升级时保留原记录，迁移失败可回滚重试。"""

import sqlite3
from datetime import date
from uuid import uuid4

import pytest

from app.models import EntryRecord, EntryStatus, EntryType, Ledger, Membership, RecordVersion, Role, User
from app.models.entry_workflow import Review
from app.storage import schema
from app.storage.database import Database
from app.storage.store import LedgerStore


def seed_version_one(database):
    with database.connect() as connection:
        for statement in schema.STATEMENTS:
            connection.execute(statement)
        connection.execute("INSERT INTO users VALUES (?, ?, ?)",
                           ("00000000-0000-0000-0000-000000000001", "保留原用户",
                            "2026-11-21T08:00:00.000000+00:00"))
        connection.execute("PRAGMA user_version=1")


def test_version_one_upgrade_preserves_existing_user(tmp_path):
    database = Database(tmp_path / "old.sqlite3")
    seed_version_one(database)
    assert database.initialize() == schema.SCHEMA_VERSION
    assert database.initialize() == schema.SCHEMA_VERSION
    with database.connect(write=False) as connection:
        assert connection.execute("SELECT name FROM users").fetchone()[0] == "保留原用户"
        assert connection.execute("SELECT count(*) FROM accounts").fetchone()[0] == 0
        assert connection.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION


def test_failed_version_two_upgrade_preserves_version_one(tmp_path, monkeypatch):
    database = Database(tmp_path / "failed-upgrade.sqlite3")
    seed_version_one(database)
    monkeypatch.setattr(schema, "AUTH_STATEMENTS", (
        "CREATE TABLE partial_auth(id INTEGER)", "NOT VALID SQL",
    ))
    with pytest.raises(sqlite3.OperationalError):
        database.initialize()
    with database.connect(write=False) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 1
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='partial_auth'"
        ).fetchall() == []


@pytest.mark.parametrize("old_version", [2, 3])
def test_upgrade_keeps_existing_entry_version_and_reviews(tmp_path, monkeypatch, old_version):
    database = Database(tmp_path / "previous.sqlite3")
    store = LedgerStore(database)
    ledger = Ledger(name="原账本")
    user = User(name="原用户")
    owner = Membership(ledger_id=ledger.id, user_id=user.id, role=Role.OWNER)
    reviewer_user = User(name="原审核人")
    reviewer = Membership(ledger_id=ledger.id, user_id=reviewer_user.id, role=Role.REVIEWER)
    entry = EntryRecord(
        ledger_id=ledger.id, submitter_member_id=owner.id,
        kind=EntryType.INCOME, request_id=uuid4(),
    )
    content = RecordVersion(
        entry_id=entry.id, ledger_id=ledger.id, number=1, amount_cents=1234,
        category="活动", purpose="原账目", business_date=date(2026, 11, 21),
        payer_member_id=owner.id,
    )
    recorded = ()
    with monkeypatch.context() as previous:
        previous.setattr(schema, "SCHEMA_VERSION", old_version)
        with store.transaction() as repositories:
            repositories.users.add(user)
            repositories.ledgers.create(ledger, owner)
            # 旧版本的写入格式固定，不能使用已升级的仓储来模拟历史程序。
            with_data = (str(entry.id), str(ledger.id), str(owner.id), "income",
                         str(entry.request_id), "approved" if old_version == 3 else "pending",
                         1, 1, None, entry.created_at.isoformat())
            connection = repositories.entries._connection
            connection.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?)", with_data)
            connection.execute("INSERT INTO entry_versions VALUES (?,?,?,?,?,?,?,?,?,?)", (
                str(entry.id), str(ledger.id), 1, 1234, "活动", "原账目", "2026-11-21",
                str(owner.id), "", content.created_at.isoformat(),
            ))
            if old_version == 3:
                repositories.users.add(reviewer_user)
                repositories.ledgers.add_member(reviewer)
                approval = Review(entry_id=entry.id, ledger_id=ledger.id, version_number=1,
                                  reviewer_member_id=reviewer.id, result=EntryStatus.APPROVED,
                                  reason="原审核记录", reviewed_at=content.created_at)
                repositories.entry_workflow.save_review(approval)
                recorded = (approval,)
    assert database.initialize() == schema.SCHEMA_VERSION
    with store.transaction(write=False) as repositories:
        assert repositories.entries.snapshot(entry.id).content == content
        assert repositories.entry_workflow.reviews(entry.id) == recorded
        assert repositories.ledgers.get_member(owner.id) == owner
        assert repositories.receipts.version_metadata(entry) == {}
