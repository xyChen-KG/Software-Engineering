"""仅在 pytest 临时目录创建账本数据，不写入实际运行数据库。"""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.application import create_app
from app.config import Settings
from app.models import EntryRecord, EntryType, Ledger, Membership, RecordVersion, Role, Split, User
from app.storage.database import Database
from app.storage.store import LedgerStore


@pytest.fixture
def book(tmp_path):
    database = Database(tmp_path / "ledger.sqlite3")
    store = LedgerStore(database)
    users = [User(name=name) for name in ("陈星宇", "饶昆忠", "林晓")]
    ledger = Ledger(name="秋日野餐", budget_cents=100_000)
    owner, reviewer, member = (
        Membership(user_id=user.id, ledger_id=ledger.id, role=role)
        for user, role in zip(users, (Role.OWNER, Role.REVIEWER, Role.MEMBER), strict=True)
    )
    with store.transaction() as repositories:
        for user in users:
            repositories.users.add(user)
        repositories.ledgers.create(ledger, owner)
        repositories.ledgers.add_member(reviewer)
        repositories.ledgers.add_member(member)
    return SimpleNamespace(
        database=database, store=store, users=users, ledger=ledger,
        owner=owner, reviewer=reviewer, member=member,
    )


@pytest.fixture
def expense(book):
    record = EntryRecord(
        ledger_id=book.ledger.id, submitter_member_id=book.reviewer.id,
        kind=EntryType.EXPENSE, request_id=uuid4(),
    )
    content = RecordVersion(
        entry_id=record.id, ledger_id=book.ledger.id, number=1, amount_cents=24_000,
        category="餐饮", purpose="水果采购", business_date=date(2026, 11, 21),
        payer_member_id=book.reviewer.id, no_receipt_reason="测试用无凭证说明",
        splits=(
            Split(member_id=book.owner.id, amount_cents=12_000),
            Split(member_id=book.reviewer.id, amount_cents=12_000),
        ),
    )
    return record, content


TEST_PASSWORD = "temporary-course-passphrase-2026"


@pytest.fixture
def api(tmp_path):
    """测试时钟可推进，不用 sleep 等待会话和邀请过期。"""
    clock = SimpleNamespace(now=datetime(2026, 11, 21, 8, tzinfo=UTC))
    settings = Settings(data_dir=tmp_path)
    application = create_app(settings, clock=lambda: clock.now)
    with TestClient(application) as client:
        yield SimpleNamespace(client=client, app=application, settings=settings, clock=clock)


@pytest.fixture
def login(api):
    def perform(username):
        response = api.client.post("/auth/login", json={
            "username": username, "password": TEST_PASSWORD,
        })
        assert response.status_code == 200, response.text
        body = response.json()
        return SimpleNamespace(
            username=username, id=body["user"]["id"], token=body["access_token"],
            headers={"Authorization": "Bearer " + body["access_token"]},
        )
    return perform


@pytest.fixture
def signup(api, login):
    def perform(username):
        response = api.client.post("/auth/register", json={
            "username": username, "password": TEST_PASSWORD, "name": username,
        })
        assert response.status_code == 201, response.text
        return login(username)
    return perform


@pytest.fixture
def group(api, signup):
    """四个真实注册账号，其中前三个构成三种账本身份。"""
    owner, reviewer, member, outsider = (signup(name) for name in (
        "owner", "reviewer", "member", "outsider",
    ))
    response = api.client.post("/ledgers", headers=owner.headers,
                               json={"name": "共享活动", "budget_cents": 100_000})
    assert response.status_code == 201
    ledger_id = response.json()["id"]
    owner.member_id = response.json()["member_id"]
    for actor, role in ((reviewer, "reviewer"), (member, "member")):
        response = api.client.post(f"/ledgers/{ledger_id}/invitations", headers=owner.headers,
                                   json={"username": actor.username, "role": role})
        assert response.status_code == 201, response.text
        actor.invitation_id = response.json()["id"]
        response = api.client.post(f"/invitations/{actor.invitation_id}/accept", headers=actor.headers)
        assert response.status_code == 200, response.text
        actor.member_id = response.json()["id"]
    return SimpleNamespace(owner=owner, reviewer=reviewer, member=member,
                           outsider=outsider, ledger_id=ledger_id)
