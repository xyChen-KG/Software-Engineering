"""预算跨档、角色交接与未读提醒使用同一个真实数据库事务。"""

from uuid import uuid4

from test_change_api import correct_body, decide
from test_entry_api import created, entry_api, review

from app.errors import ConflictError
from app.storage.audit import AuditRepository
from app.storage.database import Database

INBOX = "/me/budget-alerts"


def inbox(client, actor):
    reply = client.get(INBOX, headers=actor.headers)
    reply.raise_for_status()
    assert reply.headers["Cache-Control"] == "no-store"
    return reply.json()


def set_budget(client, group, amount):
    client.patch(f"/ledgers/{group.ledger_id}", headers=group.owner.headers,
                 json={"budget_cents": amount}).raise_for_status()


def test_threshold_and_independent_recipient_acknowledgement(entry_api, group):
    client, base, draft = entry_api
    set_budget(client, group, 30000)
    record = created(client, base, group.member, draft)
    assert inbox(client, group.owner)["count"] == 0
    review(client, base, group.reviewer, record).raise_for_status()
    assert inbox(client, group.owner)["items"][0]["level"] == "warning"
    assert inbox(client, group.reviewer)["count"] == 0
    set_budget(client, group, 24000)
    assert inbox(client, group.owner)["count"] == 1
    set_budget(client, group, 23000)
    alert = inbox(client, group.owner)["items"][0]
    assert (alert["level"], alert["expense"], alert["budget"]) == ("over", 24000, 23000)
    assert inbox(client, group.reviewer)["count"] == 1
    assert inbox(client, group.member)["count"] == inbox(client, group.outsider)["count"] == 0
    url = f"{INBOX}/{alert['id']}/read"
    client.post(url, headers=group.member.headers).raise_for_status()
    assert inbox(client, group.owner)["count"] == 2
    for _ in range(2):
        client.post(url, headers=group.owner.headers).raise_for_status()
    assert inbox(client, group.owner)["count"] == inbox(client, group.reviewer)["count"] == 1
    set_budget(client, group, 20000)
    assert inbox(client, group.owner)["count"] == 1
    for amount in (0, None, 30000):
        set_budget(client, group, amount)
    assert [a["level"] for a in inbox(client, group.owner)["items"]] == ["warning", "warning"]


def test_permission_reason_in_audit_and_new_recipient(entry_api, group):
    client, base, draft = entry_api
    ledger = base.removesuffix("/entries")
    record = created(client, base, group.member, draft)
    review(client, base, group.owner, record).raise_for_status()
    set_budget(client, group, 20000)
    url = f"{ledger}/members/{group.member.member_id}"
    for patch in ({}, {"reason": "  "}, {"reason": "长" * 501}):
        assert client.patch(url, headers=group.owner.headers,
                            json={"role": "reviewer"} | patch).status_code == 422
    assert inbox(client, group.member)["count"] == 0
    for _ in range(2):
        client.patch(url, headers=group.owner.headers,
                     json={"role": "reviewer", "reason": "  活动轮值  "}).raise_for_status()
    assert inbox(client, group.member)["count"] == inbox(client, group.reviewer)["count"] == 1
    logs = client.get(ledger + "/logs", headers=group.owner.headers,
                      params={"action": "membership.role_changed"}).json()
    assert len(logs) == 1
    assert logs[0]["details"] == {"before": "member", "after": "reviewer", "reason": "活动轮值"}
    client.patch(url, headers=group.owner.headers,
                 json={"role": "member", "reason": "轮值结束"}).raise_for_status()
    assert inbox(client, group.member)["count"] == 0
    assert client.post(ledger + "/transfer-owner", headers=group.owner.headers,
                       json={"member_id": group.member.member_id}).status_code == 422
    client.post(ledger + "/transfer-owner", headers=group.owner.headers,
                json={"member_id": group.member.member_id, "reason": "负责人换届"}).raise_for_status()
    assert inbox(client, group.owner)["count"] == 0 and inbox(client, group.member)["count"] == 1
    logs = client.get(ledger + "/logs", headers=group.member.headers,
                      params={"action": "ledger.owner_transferred"}).json()
    assert logs[0]["details"]["reason"] == "负责人换届"


def test_correction_then_void_rearms_warning(entry_api, group):
    client, base, draft = entry_api
    set_budget(client, group, 25000)
    record = created(client, base, group.member, draft)
    record = review(client, base, group.owner, record).json()["record"]
    url = f"{base}/{record['id']}"
    proposal = client.post(url + "/changes", headers=group.member.headers,
                           json=correct_body(draft, record["revision"], budget_reason="补记"))
    proposal.raise_for_status()
    assert inbox(client, group.reviewer)["count"] == 0
    decide(client, url, group.owner, proposal.json()).raise_for_status()
    assert inbox(client, group.reviewer)["items"][0]["expense"] == 26000
    record = client.get(url, headers=group.member.headers).json()["detail"]["snapshot"]["record"]
    void = client.post(url + "/changes", headers=group.member.headers,
                       json={"revision": record["revision"], "kind": "void", "reason": "作废"})
    decide(client, url, group.owner, void.json()).raise_for_status()
    retry = created(client, base, group.member, draft | {"request_id": str(uuid4())})
    review(client, base, group.owner, retry).raise_for_status()
    assert [a["level"] for a in inbox(client, group.owner)["items"]] == ["warning", "over", "warning"]


def test_alert_rolls_back_with_approval(entry_api, group, monkeypatch):
    client, base, draft = entry_api
    set_budget(client, group, 30000)
    record = created(client, base, group.member, draft)

    def fail(*_args):
        raise ConflictError("模拟日志失败")

    with monkeypatch.context() as scoped:
        scoped.setattr(AuditRepository, "append", fail)
        assert review(client, base, group.owner, record).status_code == 409
    assert inbox(client, group.owner)["count"] == 0
    review(client, base, group.owner, record).raise_for_status()
    assert inbox(client, group.owner)["count"] == 1


def test_existing_v7_book_gets_one_baseline_alert(entry_api, group, api):
    client, base, draft = entry_api
    record = created(client, base, group.member, draft)
    review(client, base, group.owner, record).raise_for_status()
    database = Database(api.settings.database_path)
    # 仅在临时库中还原旧结构；旧版没有提醒表。
    with database.connect() as connection:
        connection.execute("DROP TABLE budget_alerts")
        connection.execute("PRAGMA user_version=7")
        connection.execute("UPDATE ledgers SET budget_cents=18000 WHERE id=?", (group.ledger_id,))
    for _ in range(2):
        database.initialize()
    assert inbox(client, group.owner)["count"] == inbox(client, group.reviewer)["count"] == 1
    assert inbox(client, group.member)["count"] == 0
