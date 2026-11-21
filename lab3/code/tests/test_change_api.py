"""验证凭证授权及更正、作废对正式账务的影响。"""

from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import pytest
from PIL import Image
from test_entry_api import created, edit_body, entry_api, review, summary

from app.errors import ConflictError
from app.storage.audit import AuditRepository
from app.storage.database import Database


def attachment(filename, image_format="PNG", color="white"):
    data = BytesIO()
    with Image.new("RGB", (12, 8), color) as image:
        image.save(data, format=image_format)
    return {"filename": filename, "data_base64": b64encode(data.getvalue()).decode("ascii")}


def read_detail(client, url, actor):
    response = client.get(url, headers=actor.headers)
    assert response.status_code == 200, response.text
    return response.json()["detail"]


def correct_body(payload, revision, **options):
    return edit_body(payload, revision, kind="correct", reason="核对付款凭证",
                     amount_cents=26_000, splits=[
                         {**part, "amount_cents": 13_000} for part in payload["splits"]
                     ], **options)


def decide(client, url, actor, change, result="approved", reason=""):
    return client.post(f"{url}/changes/{change['id']}/review", headers=actor.headers,
                       json={"result": result, "reason": reason})


def test_receipts_are_versioned_and_require_active_membership(entry_api, group):
    client, base, payload = entry_api
    original = attachment("采购收据.png")
    body = payload | {"attachments": [original], "no_receipt_reason": ""}
    record = created(client, base, group.member, body)
    assert created(client, base, group.member, body)["id"] == record["id"]
    other = attachment("收据补拍.jpg", "JPEG", "lightblue")
    assert client.post(base, headers=group.member.headers, json=body | {
        "attachments": [other],
    }).status_code == 409
    url = f"{base}/{record['id']}"
    initial = read_detail(client, url, group.member)["receipts"]["1"][0]
    assert initial["mime"] == "image/png"
    receipt_url = f"{url}/receipts/{initial['id']}"
    assert client.get(receipt_url, headers=group.outsider.headers).status_code == 403
    photo = client.get(receipt_url, headers=group.reviewer.headers)
    assert photo.headers["cache-control"] == "no-store"
    assert photo.json()["data_base64"] == original["data_base64"]

    update = edit_body(body, 1, attachments=[other], keep_receipts=False)
    assert client.patch(url, headers=group.member.headers, json=update).status_code == 200
    files = read_detail(client, url, group.owner)["receipts"]
    assert files["1"] == [initial] and files["2"][0]["filename"] == other["filename"]
    assert files["2"][0]["mime"] == "image/jpeg"
    member_url = f"/ledgers/{group.ledger_id}/members/{group.member.member_id}"
    assert client.delete(member_url, headers=group.owner.headers).status_code == 200
    assert client.get(receipt_url, headers=group.member.headers).status_code == 403
    assert client.get(receipt_url, headers=group.owner.headers).status_code == 200


@pytest.mark.parametrize("invalid", [
    [{"filename": "凭证.jpg", "data_base64": "bGlzdA=="}],
    [{"filename": "../凭证.png", "data_base64": "aW1hZ2U="}],
    [{"filename": "凭证.png", "data_base64": "%invalid%"}],
    [{"filename": "文件.png", "data_base64": "YQ=="}] * 4,
])
def test_invalid_receipt_leaves_no_record_or_blob(entry_api, group, api, invalid):
    client, base, payload = entry_api
    assert client.post(base, headers=group.member.headers, json=payload | {
        "attachments": invalid, "no_receipt_reason": "",
    }).status_code == 422
    assert client.get(base, headers=group.owner.headers).json()["entries"]["items"] == []
    with Database(api.settings.database_path).connect(write=False) as conn:
        assert conn.execute("SELECT count(*) FROM receipt_files").fetchone()[0] == 0


def test_change_rejection_correction_and_void_preserve_evidence(entry_api, group):
    client, base, payload = entry_api
    original, new = attachment("原收据.png"), attachment("新收据.jpg", "JPEG", "yellow")
    record = created(client, base, group.member, payload | {
        "attachments": [original], "no_receipt_reason": "",
    })
    formal = review(client, base, group.owner, record).json()["record"]
    url = f"{base}/{formal['id']}"
    changes = url + "/changes"
    proposal = correct_body(payload, formal["revision"], attachments=[new],
                            keep_receipts=False, no_receipt_reason="")
    assert client.post(changes, headers=group.reviewer.headers, json=proposal).status_code == 403
    response = client.post(changes, headers=group.member.headers, json=proposal)
    assert response.status_code == 201, response.text
    first = response.json()
    assert client.post(changes, headers=group.owner.headers, json=proposal).status_code == 409
    queue_url = f"/ledgers/{group.ledger_id}/changes"
    assert client.get(queue_url, headers=group.member.headers).status_code == 403
    assert client.get(queue_url, headers=group.reviewer.headers).json()["items"][0]["id"] == first["id"]
    pending = read_detail(client, url, group.member)
    assert pending["snapshot"]["record"]["current_version"] == 1
    assert pending["changes"][0]["receipts"][0]["filename"] == new["filename"]
    assert summary(client, base, group.owner)["expense_cents"] == 24_000

    assert decide(client, url, group.owner, first, "rejected").status_code == 422
    rejected = decide(client, url, group.owner, first, "rejected", "补交付款说明")
    assert rejected.status_code == 200 and rejected.json()["review_reason"] == "补交付款说明"
    assert summary(client, base, group.owner)["expense_cents"] == 24_000

    response = client.post(changes, headers=group.owner.headers, json=proposal)
    assert response.status_code == 201, response.text
    second = response.json()
    assert decide(client, url, group.owner, second).status_code == 403
    assert decide(client, url, group.reviewer, second).status_code == 200
    corrected = read_detail(client, url, group.member)
    assert summary(client, base, group.owner)["expense_cents"] == 26_000
    assert [item["amount_cents"] for item in corrected["history"]] == [24_000, 26_000]
    assert [item["request"]["status"] for item in corrected["changes"]] == ["approved", "rejected"]
    for number, image in (("1", original), ("2", new)):
        file = corrected["receipts"][number][0]
        current = client.get(f"{url}/receipts/{file['id']}", headers=group.member.headers)
        assert current.json()["filename"] == image["filename"]
        assert current.json()["data_base64"] == image["data_base64"]

    response = client.post(changes, headers=group.member.headers, json={
        "kind": "void", "reason": "对应业务已取消",
        "revision": corrected["snapshot"]["record"]["revision"],
    })
    assert response.status_code == 201, response.text
    assert summary(client, base, group.owner)["expense_cents"] == 26_000
    assert decide(client, url, group.owner, response.json()).status_code == 200
    final = read_detail(client, url, group.member)
    assert final["snapshot"]["record"]["status"] == "voided"
    assert final["receipts"] == corrected["receipts"]
    assert summary(client, base, group.owner)["expense_cents"] == 0
    assert client.get(queue_url, headers=group.reviewer.headers).json()["items"] == []


def test_approval_rolls_back_on_failed_log_and_only_one_reviewer_can_retry(
    entry_api, group, monkeypatch,
):
    client, base, payload = entry_api
    first = created(client, base, group.member, payload)
    formal = review(client, base, group.owner, first).json()["record"]
    url = f"{base}/{formal['id']}"
    proposal = correct_body(payload, formal["revision"], attachments=[attachment("收据.png")])
    response = client.post(f"{url}/changes", headers=group.member.headers, json=proposal)
    assert response.status_code == 201, response.text
    pending = response.json()

    def fail(_self, _event):
        raise ConflictError("模拟日志写入失败")

    with monkeypatch.context() as patch:
        patch.setattr(AuditRepository, "append", fail)
        assert decide(client, url, group.owner, pending).status_code == 409
    detail = read_detail(client, url, group.member)
    assert detail["changes"][0]["request"]["status"] == "pending"
    assert len(detail["history"]) == 1 and "2" not in detail["receipts"]
    assert summary(client, base, group.owner)["expense_cents"] == 24_000

    with ThreadPoolExecutor(max_workers=2) as executor:
        decisions = [executor.submit(decide, client, url, actor, pending)
                     for actor in (group.owner, group.reviewer)]
        assert sorted(item.result().status_code for item in decisions) == [200, 409]
    detail = read_detail(client, url, group.member)
    assert len(detail["history"]) == 2
    assert summary(client, base, group.owner)["expense_cents"] == 26_000
    logs = client.get(f"/ledgers/{group.ledger_id}/logs", headers=group.owner.headers,
                      params={"action": "change.approved"}).json()
    assert len(logs) == 1
    assert logs[0]["details"]["before"]["amount_cents"] == 24_000
    assert logs[0]["details"]["after"]["amount_cents"] == 26_000
    conflicts = client.get(f"/ledgers/{group.ledger_id}/logs", headers=group.owner.headers,
                           params={"action": "change.conflict"}).json()
    assert len(conflicts) == 1 and conflicts[0]["details"]["change_id"] == pending["id"]
