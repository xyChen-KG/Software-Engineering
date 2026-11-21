"""金额、版本关系和分摊约束的领域测试。"""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime
from uuid import uuid4

import pytest

from app.errors import ValidationError
from app.models import AuditEvent, EntrySnapshot, EntryType, Ledger, Split, User


@pytest.mark.parametrize("amount", [0, -1, True, 1.5, "24000", 2**63])
def test_amount_must_be_positive_integer_cents(expense, amount):
    _, content = expense
    with pytest.raises(ValidationError, match="金额分"):
        replace(content, amount_cents=amount)


@pytest.mark.parametrize("budget", [None, 0, 100_000])
def test_budget_can_be_unset_or_zero(budget):
    assert Ledger(name="活动", budget_cents=budget).budget_cents == budget


@pytest.mark.parametrize("budget", [-1, True, 1.1])
def test_invalid_budget_is_rejected(budget):
    with pytest.raises(ValidationError):
        Ledger(name="活动", budget_cents=budget)


def test_split_total_and_duplicate_members_are_rejected(expense):
    record, content = expense
    member_id = content.splits[0].member_id
    with pytest.raises(ValidationError, match="重复"):
        replace(content, splits=(Split(member_id=member_id, amount_cents=12_000),) * 2)
    wrong = replace(content, splits=(Split(member_id=member_id, amount_cents=1),))
    with pytest.raises(ValidationError, match="合计"):
        EntrySnapshot(record=record, content=wrong)
    with pytest.raises(ValidationError, match="必须填写分摊"):
        EntrySnapshot(record=record, content=replace(content, splits=()))


def test_income_has_no_expense_splits(expense):
    record, content = expense
    income = replace(record, kind=EntryType.INCOME)
    with pytest.raises(ValidationError, match="收入不填写"):
        EntrySnapshot(record=income, content=content)
    assert EntrySnapshot(record=income, content=replace(content, splits=())).content.amount_cents > 0


@pytest.mark.parametrize("field,value", [("entry_id", uuid4()), ("ledger_id", uuid4()), ("number", 2)])
def test_current_content_must_match_entry_header(expense, field, value):
    record, content = expense
    with pytest.raises(ValidationError, match="不一致"):
        EntrySnapshot(record=record, content=replace(content, **{field: value}))


def test_objects_are_immutable_and_timestamps_are_aware():
    user = User(name="同名用户")
    with pytest.raises(FrozenInstanceError):
        user.name = "覆盖原内容"
    with pytest.raises(ValidationError, match="时区"):
        User(name="用户", created_at=datetime(2026, 11, 21))
    with pytest.raises(ValidationError, match="不能为空"):
        User(name="  ")


def test_business_date_rejects_datetime(expense):
    _, content = expense
    with pytest.raises(ValidationError, match="业务日期"):
        replace(content, business_date=datetime(2026, 11, 21))


@pytest.mark.parametrize("details", ["not-json", "[]", "null", '{"amount":NaN}'])
def test_audit_details_must_be_a_json_object(book, details):
    with pytest.raises(ValidationError):
        AuditEvent(
            ledger_id=book.ledger.id, actor_member_id=book.owner.id,
            object_id=book.ledger.id, action="ledger.created", details=details,
        )
