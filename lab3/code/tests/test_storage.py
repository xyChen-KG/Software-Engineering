"""仓储集成测试：角色范围、请求去重、历史、冲突与一致性。"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

import pytest

from app.errors import ConflictError, NotFoundError, ValidationError
from app.models import (
    AuditEvent, EntryStatus, EntryType, Ledger, Membership, MembershipStatus, Role, Split, User,
)


def test_one_user_can_have_different_roles_in_different_ledgers(book):
    second = Ledger(name="另一场活动", budget_cents=0)
    owner = Membership(user_id=book.users[2].id, ledger_id=second.id, role=Role.OWNER)
    reviewer = Membership(user_id=book.users[0].id, ledger_id=second.id, role=Role.REVIEWER)
    with book.store.transaction() as repositories:
        repositories.ledgers.create(second, owner)
        repositories.ledgers.add_member(reviewer)
        assert repositories.ledgers.get_member(book.owner.id).role == Role.OWNER
        assert repositories.ledgers.get_member(reviewer.id).role == Role.REVIEWER
        assert {ledger.id for ledger in repositories.ledgers.for_user(book.users[0].id)} == {
            book.ledger.id, second.id,
        }
        # 姓名可以相同，用户及成员关系的业务编号不相同。
        other = User(name=book.users[0].name)
        repositories.users.add(other)
        assert repositories.users.get(other.id).name == book.users[0].name


def test_same_user_cannot_be_added_twice_to_one_ledger(book):
    duplicate = replace(book.member, id=uuid4())
    with book.store.transaction() as repositories:
        with pytest.raises(ConflictError):
            repositories.ledgers.add_member(duplicate)
        assert len(repositories.ledgers.members(book.ledger.id)) == 3


def test_database_rejects_two_active_owners(book):
    with pytest.raises(sqlite3.IntegrityError):
        with book.database.connect() as connection:
            connection.execute(
                "UPDATE memberships SET role='owner' WHERE id=?", (str(book.member.id),)
            )


def test_save_and_reopen_persists_entry_and_splits(book, expense):
    record, content = expense
    with book.store.transaction() as repositories:
        saved = repositories.entries.add(record, content)
        assert saved.record == record
    with book.store.transaction(write=False) as repositories:
        loaded = repositories.entries.snapshot(record.id)
        assert loaded.content.amount_cents == 24_000
        assert {split.member_id: split.amount_cents for split in loaded.content.splits} == {
            book.owner.id: 12_000, book.reviewer.id: 12_000,
        }
        assert repositories.entries.list_headers(book.ledger.id, status=EntryStatus.PENDING) == (
            loaded.record,
        )
        assert repositories.entries.list_headers(book.ledger.id, status=EntryStatus.APPROVED) == ()


def test_repeated_request_with_regenerated_ids_creates_only_one_entry(book, expense):
    record, content = expense
    retry = replace(record, id=uuid4())
    with book.store.transaction() as repositories:
        first = repositories.entries.add(record, content)
        second = repositories.entries.add(retry, replace(content, entry_id=retry.id))
        assert first == second
        assert len(repositories.entries.list_headers(book.ledger.id)) == 1
        assert len(repositories.entries.history(first.record.id)) == 1


def test_same_request_cannot_silently_change_payload(book, expense):
    record, content = expense
    changed = replace(content, purpose="相同请求却换了用途")
    with book.store.transaction() as repositories:
        repositories.entries.add(record, content)
        with pytest.raises(ConflictError, match="请求编号"):
            repositories.entries.add(record, changed)
        assert repositories.entries.snapshot(record.id).content.purpose == content.purpose


def test_simultaneous_duplicate_requests_share_one_record(book, expense):
    record, content = expense
    barrier = Barrier(2)

    def submit(_):
        retry = replace(record, id=uuid4())
        barrier.wait(timeout=5)
        with book.store.transaction() as repositories:
            return repositories.entries.add(retry, replace(content, entry_id=retry.id)).record.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(submit, range(2)))
    assert ids[0] == ids[1]
    with book.store.transaction(write=False) as repositories:
        assert len(repositories.entries.list_headers(book.ledger.id)) == 1
        assert len(repositories.entries.history(ids[0])) == 1


def test_simultaneous_edits_only_one_expected_revision_succeeds(book, expense):
    record, content = expense
    with book.store.transaction() as repositories:
        repositories.entries.add(record, content)
    barrier = Barrier(2)

    def edit(amount):
        updated = replace(content, number=2, amount_cents=amount, splits=tuple(
            replace(split, amount_cents=amount // 2) for split in content.splits
        ))
        barrier.wait(timeout=5)
        try:
            with book.store.transaction() as repositories:
                repositories.entries.append_version(updated, expected_revision=1)
            return "saved"
        except ConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(edit, (26_000, 28_000)))
    assert sorted(results) == ["conflict", "saved"]
    with book.store.transaction(write=False) as repositories:
        current = repositories.entries.snapshot(record.id)
        assert current.record.revision == 2
        assert current.content.amount_cents in {26_000, 28_000}
        assert len(repositories.entries.history(record.id)) == 2


def test_edit_keeps_history_and_rejects_stale_revision(book, expense):
    record, content = expense
    updated = replace(content, number=2, amount_cents=26_000, splits=tuple(
        replace(split, amount_cents=13_000) for split in content.splits
    ))
    with book.store.transaction() as repositories:
        repositories.entries.add(record, content)
        latest = repositories.entries.append_version(updated, expected_revision=1)
        assert latest.record.id == record.id
        assert latest.record.revision == 2
        assert latest.record.current_version == 2
        assert latest.content.amount_cents == 26_000
    with book.store.transaction() as repositories:
        with pytest.raises(ConflictError, match="最新版本"):
            repositories.entries.append_version(replace(updated, number=3), expected_revision=1)
        history = repositories.entries.history(record.id)
        assert [version.amount_cents for version in history] == [24_000, 26_000]
        assert sum(split.amount_cents for split in history[0].splits) == 24_000
        # 重试最初登记请求，不会把已更新的内容覆盖回版本一。
        replay = repositories.entries.add(record, content)
        assert replay.record.revision == 2
        assert replay.content.amount_cents == 26_000


def test_approved_records_cannot_be_directly_edited(book, expense):
    record, content = expense
    with book.store.transaction() as repositories:
        repositories.entries.add(record, content)
    # 这里只构造存储状态，不表示审核业务已实现。
    with book.database.connect() as connection:
        connection.execute(
            "UPDATE entries SET status='approved', revision=2 WHERE id=?", (str(record.id),)
        )
    with book.store.transaction() as repositories:
        with pytest.raises(ConflictError, match="状态"):
            repositories.entries.append_version(replace(content, number=2), expected_revision=2)
        assert len(repositories.entries.history(record.id)) == 1


@pytest.mark.parametrize("column", ["current_version", "revision"])
def test_database_rejects_fractional_versions(book, expense, column):
    record, content = expense
    with book.store.transaction() as repositories:
        repositories.entries.add(record, content)
    with pytest.raises(sqlite3.IntegrityError):
        with book.database.connect() as connection:
            connection.execute(f"UPDATE entries SET {column}=1.5 WHERE id=?", (str(record.id),))


@pytest.mark.parametrize("target", ["payer", "split"])
def test_cross_ledger_members_cannot_be_used(book, expense, target):
    record, content = expense
    other = Ledger(name="其他账本")
    outsider = Membership(user_id=book.users[0].id, ledger_id=other.id, role=Role.OWNER)
    with book.store.transaction() as repositories:
        repositories.ledgers.create(other, outsider)
        bad = replace(content, payer_member_id=outsider.id) if target == "payer" else replace(
            content, splits=(Split(member_id=outsider.id, amount_cents=24_000),)
        )
        with pytest.raises(ValidationError, match="同一账本"):
            repositories.entries.add(record, bad)
        assert repositories.entries.list_headers(book.ledger.id) == ()


def test_composite_foreign_key_rejects_cross_ledger_split_even_with_raw_sql(book, expense):
    record, content = expense
    other = Ledger(name="不能串账")
    outsider = Membership(user_id=book.users[0].id, ledger_id=other.id, role=Role.OWNER)
    with book.store.transaction() as repositories:
        repositories.ledgers.create(other, outsider)
        repositories.entries.add(record, content)
    with pytest.raises(sqlite3.IntegrityError):
        with book.database.connect() as connection:
            connection.execute(
                "INSERT INTO splits VALUES (?, ?, ?, ?, ?)",
                (str(record.id), 1, str(book.ledger.id), str(outsider.id), 0),
            )


def test_exit_hides_ledger_but_keeps_old_records(book, expense):
    record, content = expense
    with book.store.transaction() as repositories:
        repositories.entries.add(record, content)
    with book.database.connect() as connection:
        connection.execute(
            "UPDATE memberships SET status='exited' WHERE id=?", (str(book.reviewer.id),)
        )
    with book.store.transaction() as repositories:
        assert repositories.ledgers.for_user(book.users[1].id) == ()
        assert len(repositories.ledgers.members(book.ledger.id)) == 2
        assert len(repositories.ledgers.members(book.ledger.id, include_exited=True)) == 3
        assert repositories.ledgers.get_member(book.reviewer.id).status == MembershipStatus.EXITED
        assert repositories.entries.snapshot(record.id).content.payer_member_id == book.reviewer.id
        retry = replace(record, id=uuid4(), request_id=uuid4())
        with pytest.raises(ValidationError, match="在册"):
            repositories.entries.add(retry, replace(content, entry_id=retry.id))


def test_archived_ledger_cannot_accept_new_entries(book, expense):
    record, content = expense
    with book.database.connect() as connection:
        connection.execute("UPDATE ledgers SET archived=1 WHERE id=?", (str(book.ledger.id),))
    with book.store.transaction() as repositories:
        with pytest.raises(ConflictError, match="归档"):
            repositories.entries.add(record, content)


def test_refund_must_link_to_approved_expense_in_same_ledger(book, expense):
    record, content = expense
    income = replace(record, id=uuid4(), request_id=uuid4(), kind=EntryType.INCOME,
                     original_expense_id=record.id)
    refund = replace(content, entry_id=income.id, splits=())
    with book.store.transaction() as repositories:
        repositories.entries.add(record, content)
        with pytest.raises(ValidationError, match="原支出"):
            repositories.entries.add(income, refund)
    with book.database.connect() as connection:
        connection.execute("UPDATE entries SET status='approved' WHERE id=?", (str(record.id),))
    with book.store.transaction() as repositories:
        saved = repositories.entries.add(income, refund)
        assert saved.record.original_expense_id == record.id
        assert saved.content.amount_cents == 24_000


def test_entry_and_audit_rollback_together(book, expense):
    record, content = expense
    invalid_event = AuditEvent(
        ledger_id=book.ledger.id, actor_member_id=uuid4(), object_id=record.id,
        action="entry.created",
    )
    with pytest.raises(ConflictError):
        with book.store.transaction() as repositories:
            repositories.entries.add(record, content)
            repositories.audit.append(invalid_event)
    with book.store.transaction(write=False) as repositories:
        assert repositories.entries.list_headers(book.ledger.id) == ()
        assert repositories.audit.query(book.ledger.id) == ()
        with pytest.raises(NotFoundError):
            repositories.entries.version(record.id, 1)


def test_audit_query_and_historical_rows_are_immutable(book, expense):
    record, content = expense
    event = AuditEvent(
        ledger_id=book.ledger.id, actor_member_id=book.reviewer.id, object_id=record.id,
        action="entry.created", details='{"new_amount_cents":24000}',
    )
    with book.store.transaction() as repositories:
        repositories.entries.add(record, content)
        repositories.audit.append(event)
    with book.store.transaction(write=False) as repositories:
        assert repositories.audit.query(book.ledger.id, actor_member_id=book.reviewer.id,
                                        action="entry.created") == (event,)
        assert repositories.audit.query(book.ledger.id, actor_member_id=book.owner.id) == ()
    for sql, parameters in (
        ("UPDATE entry_versions SET amount_cents=1 WHERE entry_id=?", (str(record.id),)),
        ("DELETE FROM splits WHERE entry_id=?", (str(record.id),)),
        ("UPDATE audit_events SET details='{}' WHERE id=?", (str(event.id),)),
        ("DELETE FROM audit_events WHERE id=?", (str(event.id),)),
    ):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            with book.database.connect() as connection:
                connection.execute(sql, parameters)
