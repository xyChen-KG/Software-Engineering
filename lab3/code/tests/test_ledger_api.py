"""角色权限、对象范围、邀请重放与历史保留的接口集成测试。"""

import json
from datetime import date, timedelta
from uuid import UUID, uuid4

from app.errors import ConflictError
from app.models import EntryRecord, EntryStatus, EntryType, RecordVersion, Split
from app.storage.audit import AuditRepository


def test_nonowners_can_read_but_cannot_manage(api, group):
    base = f"/ledgers/{group.ledger_id}"
    for actor in (group.reviewer, group.member):
        assert api.client.get(base, headers=actor.headers).status_code == 200
        assert api.client.get(base + "/members", headers=actor.headers).status_code == 200
        assert api.client.get(base + "/logs", headers=actor.headers).status_code == 200
        operations = (
            ("PATCH", base, {"name": "越权修改"}),
            ("POST", base + "/invitations", {"username": "outsider"}),
            ("PATCH", base + f"/members/{group.member.member_id}", {"role": "reviewer", "reason": "代班"}),
            ("POST", base + "/transfer-owner", {"member_id": group.member.member_id, "reason": "交接"}),
            ("DELETE", base + f"/members/{group.member.member_id}", None),
            ("GET", base + "/invitations", None),
        )
        for method, url, body in operations:
            response = api.client.request(method, url, json=body, headers=actor.headers)
            assert response.status_code == 403, (method, url, response.text)
    assert api.client.get(base, headers=group.owner.headers).json()["name"] == "共享活动"


def test_nonmember_and_cross_ledger_object_access_is_denied(api, group):
    base = f"/ledgers/{group.ledger_id}"
    for suffix in ("", "/members", "/logs", "/invitations"):
        assert api.client.get(base + suffix, headers=group.outsider.headers).status_code == 403
    assert api.client.get("/ledgers", headers=group.outsider.headers).json() == []
    other = api.client.post("/ledgers", headers=group.outsider.headers,
                            json={"name": "另一个账本"}).json()
    response = api.client.delete(f"/ledgers/{other['id']}/members/{group.member.member_id}",
                                 headers=group.outsider.headers)
    assert response.status_code == 404
    assert len(api.client.get(base + "/members", headers=group.owner.headers).json()) == 3


def test_settings_patch_and_input_boundaries(api, group):
    base = f"/ledgers/{group.ledger_id}"
    response = api.client.patch(base, headers=group.owner.headers, json={"name": "新名称"})
    assert response.status_code == 200 and response.json()["budget_cents"] == 100_000
    response = api.client.patch(base, headers=group.owner.headers, json={"budget_cents": None})
    assert response.status_code == 200 and response.json()["budget_cents"] is None
    for body in ({}, {"name": None}, {"archived": None}, {"budget_cents": True},
                 {"budget_cents": -1}, {"owner_id": group.outsider.id}):
        assert api.client.patch(base, headers=group.owner.headers, json=body).status_code == 422
    response = api.client.patch(base, headers=group.owner.headers, json={"budget_cents": 0})
    assert response.json()["budget_cents"] == 0


def test_creator_and_owner_role_cannot_be_spoofed_in_payload(api, group):
    response = api.client.post("/ledgers", headers=group.member.headers,
                               json={"name": "伪造", "user_id": group.owner.id})
    assert response.status_code == 422
    response = api.client.patch(
        f"/ledgers/{group.ledger_id}/members/{group.member.member_id}",
        headers=group.owner.headers, json={"role": "owner", "reason": "交接"},
    )
    assert response.status_code == 422
    response = api.client.post(f"/ledgers/{group.ledger_id}/invitations",
                               headers=group.owner.headers,
                               json={"username": "outsider", "role": "owner"})
    assert response.status_code == 422


def test_invitation_is_bound_to_target_and_replay_does_not_restore_role(api, group):
    base = f"/ledgers/{group.ledger_id}"
    response = api.client.post(base + "/invitations", headers=group.owner.headers,
                               json={"username": "outsider", "role": "reviewer"})
    assert response.status_code == 201
    invitation = response.json()
    assert api.client.post(base + "/invitations", headers=group.owner.headers,
                          json={"username": "outsider"}).status_code == 409
    inbox = api.client.get("/invitations", headers=group.outsider.headers).json()
    assert [item["id"] for item in inbox] == [invitation["id"]]
    assert inbox[0]["ledger_name"] == "共享活动" and inbox[0]["inviter_name"] == "owner"
    url = f"/invitations/{invitation['id']}/accept"
    assert api.client.post(url, headers=group.member.headers).status_code == 403
    joined = api.client.post(url, headers=group.outsider.headers)
    assert joined.status_code == 200 and joined.json()["role"] == "reviewer"
    membership_id = joined.json()["id"]
    response = api.client.patch(base + f"/members/{membership_id}", headers=group.owner.headers,
                                json={"role": "member", "reason": "值班结束"})
    assert response.status_code == 200
    replay = api.client.post(url, headers=group.outsider.headers)
    assert replay.json()["id"] == membership_id and replay.json()["role"] == "member"
    assert len(api.client.get(base + "/members", headers=group.owner.headers).json()) == 4


def test_revoked_expired_and_archived_invitations_cannot_be_accepted(api, group, login):
    base = f"/ledgers/{group.ledger_id}"

    def invite():
        result = api.client.post(base + "/invitations", headers=group.owner.headers,
                                 json={"username": "outsider"})
        assert result.status_code == 201, result.text
        return result.json()["id"]

    first = invite()
    assert api.client.delete(base + f"/invitations/{first}",
                             headers=group.owner.headers).status_code == 200
    assert api.client.post(f"/invitations/{first}/accept",
                           headers=group.outsider.headers).status_code == 409
    second = invite()
    assert api.client.patch(base, headers=group.owner.headers,
                            json={"archived": True}).status_code == 200
    assert api.client.post(f"/invitations/{second}/accept",
                           headers=group.outsider.headers).status_code == 409
    api.client.patch(base, headers=group.owner.headers, json={"archived": False})
    api.clock.now += timedelta(days=8)
    group.owner = login("owner")
    group.outsider = login("outsider")
    assert api.client.post(f"/invitations/{second}/accept",
                           headers=group.outsider.headers).status_code == 409
    third = invite()  # 过期的旧待处理邀请会保留为 revoked，新邀请可正常产生。
    assert third != second
    assert api.client.post(f"/invitations/{third}/accept",
                           headers=group.outsider.headers).status_code == 200


def test_transfer_owner_changes_permissions_and_invalidates_old_invites(api, group):
    base = f"/ledgers/{group.ledger_id}"
    invitation = api.client.post(base + "/invitations", headers=group.owner.headers,
                                 json={"username": "outsider"}).json()
    assert api.client.post(base + "/leave", headers=group.owner.headers).status_code == 409
    response = api.client.post(base + "/transfer-owner", headers=group.owner.headers,
                               json={"member_id": group.reviewer.member_id, "reason": "学期交接"})
    assert response.status_code == 200 and response.json()["role"] == "owner"
    assert api.client.patch(base, headers=group.owner.headers,
                            json={"name": "旧页面越权"}).status_code == 403
    assert api.client.patch(base, headers=group.reviewer.headers,
                            json={"name": "新负责人设置"}).status_code == 200
    assert api.client.post(f"/invitations/{invitation['id']}/accept",
                           headers=group.outsider.headers).status_code == 409
    members = api.client.get(base + "/members", headers=group.reviewer.headers).json()
    assert sum(member["role"] == "owner" for member in members) == 1


def test_log_failure_rolls_back_settings_and_owner_transfer(api, group, monkeypatch):
    def fail(_self, _event):
        raise ConflictError("模拟日志存储失败")
    monkeypatch.setattr(AuditRepository, "append", fail)
    base = f"/ledgers/{group.ledger_id}"
    response = api.client.patch(base, headers=group.owner.headers, json={"name": "不应保存"})
    assert response.status_code == 409
    assert api.client.get(base, headers=group.owner.headers).json()["name"] == "共享活动"
    response = api.client.post(base + "/transfer-owner", headers=group.owner.headers,
                               json={"member_id": group.reviewer.member_id, "reason": "学期交接"})
    assert response.status_code == 409
    assert api.client.get(base, headers=group.owner.headers).json()["role"] == "owner"
    assert api.client.get(base, headers=group.reviewer.headers).json()["role"] == "reviewer"


def test_leave_loses_access_and_old_invitation_cannot_rejoin(api, group):
    base = f"/ledgers/{group.ledger_id}"
    original = api.client.get(base + "/members", headers=group.owner.headers).json()
    assert api.client.post(base + "/leave", headers=group.member.headers).status_code == 200
    assert api.client.get(base, headers=group.member.headers).status_code == 403
    assert api.client.get(base + "/logs", headers=group.member.headers).status_code == 403
    assert api.client.get("/auth/me", headers=group.member.headers).status_code == 200
    assert api.client.get("/ledgers", headers=group.member.headers).json() == []
    assert api.client.post(f"/invitations/{group.member.invitation_id}/accept",
                           headers=group.member.headers).status_code == 409
    invitation = api.client.post(base + "/invitations", headers=group.owner.headers,
                                 json={"username": "member"}).json()
    response = api.client.post(f"/invitations/{invitation['id']}/accept",
                               headers=group.member.headers)
    assert response.status_code == 200 and response.json()["id"] == group.member.member_id
    members = api.client.get(base + "/members", headers=group.owner.headers).json()
    assert len(members) == len(original)
    original_time = next(row["joined_at"] for row in original if row["id"] == group.member.member_id)
    assert response.json()["joined_at"] == original_time


def test_pending_records_block_leave_but_removal_preserves_history(api, group):
    base = f"/ledgers/{group.ledger_id}"
    member_id = UUID(group.member.member_id)
    record = EntryRecord(ledger_id=UUID(group.ledger_id), submitter_member_id=member_id,
                         kind=EntryType.EXPENSE, request_id=uuid4())
    content = RecordVersion(
        entry_id=record.id, ledger_id=record.ledger_id, number=1, amount_cents=100,
        category="测试", purpose="仅构造退出前置条件", business_date=date(2026, 11, 21),
        payer_member_id=member_id, splits=(Split(member_id=member_id, amount_cents=100),),
    )
    with api.app.state.store.transaction() as repositories:
        repositories.entries.add(record, content)
    assert api.client.post(base + "/leave", headers=group.member.headers).status_code == 409
    assert api.client.delete(base + f"/members/{member_id}",
                             headers=group.owner.headers).status_code == 200
    for suffix in ("", "/members", "/logs"):
        assert api.client.get(base + suffix, headers=group.member.headers).status_code == 403
    with api.app.state.store.transaction(write=False) as repositories:
        current = repositories.entries.snapshot(record.id)
        assert current.record.status == EntryStatus.PENDING
        assert current.content.splits[0].member_id == member_id
    logs = api.client.get(base + "/logs", headers=group.owner.headers,
                           params={"action": "membership.removed"}).json()
    assert logs[0]["details"]["pending_records_retained"] == 1
    members = api.client.get(base + "/members", params={"include_exited": True},
                             headers=group.owner.headers).json()
    assert len(members) == 3
    assert next(row for row in members if row["id"] == str(member_id))["status"] == "exited"


def test_member_roles_are_independent_across_ledgers_and_logs_have_no_credentials(api, group):
    second = api.client.post("/ledgers", headers=group.member.headers,
                              json={"name": "另一活动"}).json()
    first = api.client.get(f"/ledgers/{group.ledger_id}", headers=group.member.headers).json()
    assert first["role"] == "member" and second["role"] == "owner"
    response = api.client.patch(f"/ledgers/{group.ledger_id}", headers=group.member.headers,
                                json={"budget_cents": 0})
    assert response.status_code == 403
    logs = api.client.get(f"/ledgers/{group.ledger_id}/logs", headers=group.owner.headers).json()
    serialized = json.dumps(logs)
    assert "password_hash" not in serialized and "access_token" not in serialized
    for actor in (group.owner, group.reviewer, group.member):
        assert actor.token not in serialized
