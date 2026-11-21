"""验证 HTTP 入口的收支状态、权限、去重及一致性。"""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.errors import ConflictError
from app.storage.audit import AuditRepository


@pytest.fixture
def entry_api(api, group):
    base = f"/ledgers/{group.ledger_id}/entries"
    payload = {
        "kind": "expense", "request_id": str(uuid4()), "amount_cents": 24_000,
        "category": "餐饮", "purpose": "水果采购", "business_date": "2026-11-21",
        "payer_member_id": group.member.member_id, "no_receipt_reason": "现金小额采购",
        "splits": [
            {"member_id": group.owner.member_id, "amount_cents": 12_000},
            {"member_id": group.member.member_id, "amount_cents": 12_000},
        ],
    }
    return api.client, base, payload


def created(client, base, actor, payload):
    response = client.post(base, headers=actor.headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()["record"]


def review(client, base, actor, record, result="approved", reason=""):
    return client.post(f"{base}/{record['id']}/review", headers=actor.headers, json={
        "revision": record["revision"], "result": result, "reason": reason,
    })


def summary(client, base, actor):
    response = client.get(base, headers=actor.headers)
    assert response.status_code == 200, response.text
    return response.json()["entries"]["summary"]


def edit_body(payload, revision, **changes):
    return {key: value for key, value in payload.items() if key not in {"request_id", "kind"}} | {
        "revision": revision, **changes,
    }


def test_expense_increases_formal_summary_only_after_other_reviewer(entry_api, group):
    client, base, payload = entry_api
    record = created(client, base, group.member, payload)
    assert record["status"] == "pending"
    assert summary(client, base, group.owner)["expense_cents"] == 0
    assert client.get(base, params={"review": True}, headers=group.member.headers).status_code == 403
    queue = client.get(base, params={"review": True}, headers=group.reviewer.headers).json()
    assert [item["id"] for item in queue["entries"]["items"]] == [record["id"]]
    response = review(client, base, group.reviewer, record)
    assert response.status_code == 200
    assert response.json()["record"]["status"] == "approved"
    assert summary(client, base, group.member) == {
        "income_cents": 0, "expense_cents": 24_000, "balance_cents": -24_000,
    }
    detail = client.get(f"{base}/{record['id']}", headers=group.member.headers).json()["detail"]
    assert detail["reviews"][0]["reviewer_member_id"] == group.reviewer.member_id
    assert detail["reviews"][0]["version_number"] == 1


def test_nonmember_self_review_and_other_edit_are_denied(entry_api, group):
    client, base, payload = entry_api
    payload = payload | {"payer_member_id": group.owner.member_id}
    record = created(client, base, group.owner, payload)
    assert review(client, base, group.owner, record).status_code == 403
    assert review(client, base, group.member, record).status_code == 403
    assert client.get(base, headers=group.outsider.headers).status_code == 403
    response = client.patch(f"{base}/{record['id']}", headers=group.reviewer.headers,
                            json=edit_body(payload, 1))
    assert response.status_code == 403
    assert summary(client, base, group.owner)["expense_cents"] == 0


def test_rejection_resubmission_keeps_id_and_both_versions(entry_api, group):
    client, base, payload = entry_api
    record = created(client, base, group.member, payload)
    url = f"{base}/{record['id']}"
    assert review(client, base, group.reviewer, record, "rejected", " ").status_code == 422
    rejected = review(client, base, group.reviewer, record, "rejected", "请改用途").json()["record"]
    assert rejected["status"] == "rejected"
    assert summary(client, base, group.member)["expense_cents"] == 0
    response = client.patch(url, headers=group.member.headers,
                            json=edit_body(payload, rejected["revision"], purpose="水果与饮料"))
    assert response.status_code == 200
    updated = response.json()["record"]
    assert updated["id"] == record["id"]
    assert updated["current_version"] == 2 and updated["status"] == "pending"
    assert review(client, base, group.reviewer, updated).status_code == 200
    assert client.patch(url, headers=group.member.headers,
                        json=edit_body(payload, updated["revision"] + 1)).status_code == 409
    detail = client.get(url, headers=group.member.headers).json()["detail"]
    assert [item["purpose"] for item in detail["history"]] == ["水果采购", "水果与饮料"]
    assert [item["result"] for item in detail["reviews"]] == ["rejected", "approved"]
    assert summary(client, base, group.member)["expense_cents"] == 24_000


def test_withdrawal_requires_submitter_reason_and_preserves_history(entry_api, group):
    client, base, payload = entry_api
    record = created(client, base, group.member, payload)
    url = f"{base}/{record['id']}/withdraw"
    assert client.post(url, headers=group.owner.headers, json={
        "revision": 1, "reason": "代撤回",
    }).status_code == 403
    assert client.post(url, headers=group.member.headers, json={
        "revision": 1, "reason": " ",
    }).status_code == 422
    response = client.post(url, headers=group.member.headers,
                           json={"revision": 1, "reason": "填写有误"})
    assert response.json()["record"]["status"] == "withdrawn"
    assert review(client, base, group.owner, record).status_code == 409
    assert summary(client, base, group.member)["expense_cents"] == 0
    logs = client.get(f"/ledgers/{group.ledger_id}/logs", headers=group.member.headers,
                      params={"action": "entry.withdrawn"}).json()
    assert logs[0]["details"]["reason"] == "填写有误"


def test_replay_returns_current_entry_and_similar_expense_requires_reason(entry_api, group):
    client, base, payload = entry_api
    original = created(client, base, group.member, payload)
    assert review(client, base, group.owner, original).status_code == 200
    replay = created(client, base, group.member, payload)
    assert replay["id"] == original["id"] and replay["status"] == "approved"
    duplicate = payload | {"request_id": str(uuid4()), "purpose": "第二次采购"}
    response = client.post(base, headers=group.member.headers, json=duplicate)
    assert response.status_code == 409 and response.json()["code"] == "possible_duplicate"
    duplicate["duplicate_reason"] = "两张独立收据"
    assert created(client, base, group.member, duplicate)["id"] != original["id"]
    logs = client.get(f"/ledgers/{group.ledger_id}/logs", headers=group.owner.headers,
                      params={"action": "entry.submitted"}).json()
    assert len(logs) == 2
    assert "两张独立收据" in {item["details"]["confirmed_duplicate_reason"] for item in logs}


@pytest.mark.parametrize("patch", [
    {"amount_cents": 0}, {"amount_cents": 1.5}, {"amount_cents": True}, {"splits": []},
    {"no_receipt_reason": ""}, {"payer_member_id": str(uuid4())},
])
def test_invalid_entry_never_creates_an_accounting_record(entry_api, group, patch):
    client, base, payload = entry_api
    response = client.post(base, headers=group.member.headers, json=payload | patch)
    assert response.status_code == 422
    assert client.get(base, headers=group.member.headers).json()["entries"]["items"] == []


def test_edit_review_race_has_only_one_winner_and_logs_conflict(entry_api, group):
    client, base, payload = entry_api
    record = created(client, base, group.member, payload)
    url = f"{base}/{record['id']}"
    with ThreadPoolExecutor(max_workers=2) as executor:
        edit = executor.submit(
            client.patch, url, headers=group.member.headers,
            json=edit_body(payload, 1, purpose="更新后的采购"),
        )
        decide = executor.submit(review, client, base, group.reviewer, record)
        assert sorted((edit.result().status_code, decide.result().status_code)) == [200, 409]
    logs = client.get(f"/ledgers/{group.ledger_id}/logs", headers=group.owner.headers,
                      params={"action": "entry.conflict"}).json()
    assert len(logs) == 1 and logs[0]["details"]["current_revision"] == 2


def test_review_and_total_rollback_if_audit_fails(entry_api, group, monkeypatch):
    client, base, payload = entry_api
    record = created(client, base, group.member, payload)

    def fail(_self, _event):
        raise ConflictError("模拟日志写入失败")

    monkeypatch.setattr(AuditRepository, "append", fail)
    assert review(client, base, group.owner, record).status_code == 409
    detail = client.get(f"{base}/{record['id']}", headers=group.member.headers).json()["detail"]
    assert detail["snapshot"]["record"]["status"] == "pending" and detail["reviews"] == []
    assert summary(client, base, group.owner)["expense_cents"] == 0


def test_reviewer_can_approve_pending_record_after_submitter_removed(entry_api, group):
    client, base, payload = entry_api
    record = created(client, base, group.member, payload)
    member_url = f"/ledgers/{group.ledger_id}/members/{group.member.member_id}"
    assert client.delete(member_url, headers=group.owner.headers).status_code == 200
    assert client.get(base, headers=group.member.headers).status_code == 403
    assert review(client, base, group.reviewer, record).status_code == 200


def test_income_and_refund_count_only_after_approval(entry_api, group):
    client, base, payload = entry_api
    original = created(client, base, group.member, payload)
    income = payload | {
        "kind": "income", "request_id": str(uuid4()), "amount_cents": 1000,
        "splits": [], "original_expense_id": original["id"], "purpose": "退款",
    }
    assert client.post(base, headers=group.member.headers, json=income).status_code == 422
    assert review(client, base, group.owner, original).status_code == 200
    refund = created(client, base, group.member, income)
    assert summary(client, base, group.owner)["income_cents"] == 0
    assert review(client, base, group.reviewer, refund).status_code == 200
    assert summary(client, base, group.owner)["balance_cents"] == -23_000
