"""跨业务模块的报表回归。"""

from datetime import timedelta
from uuid import uuid4

from test_entry_api import created, entry_api, review


def test_search_intersection_across_versions_and_pages(entry_api, group):
    client, base, draft = entry_api
    for category in ("餐饮", "交通"):
        record = created(client, base, group.member, draft | {
            "request_id": str(uuid4()), "category": category,
        })
        assert review(client, base, group.owner, record).is_success
    url = base.replace("/entries", "/search")
    def search(**params):
        return client.get(url, headers=group.member.headers, params=params)
    page = search(kind="expense", category="餐饮", status="approved", minimum=24000,
                  maximum=24000, member_id=group.member.member_id, keyword="水果",
                  start="2026-11-21", end="2026-11-21").json()
    assert len(page["items"]) == 1 and page["balance"] == -24000
    assert page["categories"] == {"餐饮": [0, 24000]}
    assert page["members"] == {group.member.member_id: [0, 24000]}
    assert search(limit=1).json()["expense"] == 48000
    assert search(keyword="%").json()["balance"] == 0
    assert search(minimum=2, maximum=1).status_code == 422
    assert client.get(url, headers=group.outsider.headers).status_code == 403


def test_budget_boundaries_and_reason(entry_api, group):
    client, base, draft = entry_api
    ledger = base.removesuffix("/entries")
    def budget():
        return client.get(ledger + "/budget", headers=group.member.headers).json()
    def limit(amount):
        assert client.patch(ledger, headers=group.owner.headers,
                            json={"budget_cents": amount}).is_success
    limit(30000)
    first = created(client, base, group.member, draft)
    assert budget()["pending"] == 24000 and budget()["expense"] == 0
    assert review(client, base, group.owner, first).is_success
    assert budget()["level"] == "warning"
    limit(24000)
    assert budget()["level"] == "warning" and budget()["excess"] == 0
    body = draft | {"request_id": str(uuid4()), "category": "交通"}
    assert client.post(base, headers=group.member.headers, json=body).status_code == 422
    second = created(client, base, group.member, body | {"budget_reason": "追加采购"})
    assert budget()["pending"] == 24000
    assert review(client, base, group.owner, second).is_success
    assert budget()["level"] == "over" and budget()["excess"] == 24000
    assert client.get(base + "/" + second["id"], headers=group.owner.headers).json()[
        "detail"]["snapshot"]["content"]["budget_reason"] == "追加采购"
    for amount in (0, None):
        limit(amount)
        assert budget()["level"] == "none"


def test_settlement_history_correction_exit_and_log_filters(entry_api, group, api):
    from test_change_api import correct_body, decide, read_detail
    from test_entry_api import summary

    client, base, draft = entry_api
    ledger = base.removesuffix("/entries")
    endpoint = ledger + "/settlements"
    def statement():
        r = client.get(endpoint, headers=group.owner.headers)
        assert r.status_code == 200, r.text
        return r.json()
    def balances():
        return {key: value["remaining"] for key, value in statement()["members"].items()}
    draft = draft | {"business_date": "2022-01-01"}
    record = created(client, base, group.member, draft)
    first = review(client, base, group.owner, record).json()["record"]
    assert balances()[group.member.member_id] == 12000
    smaller = draft | {"request_id": str(uuid4()), "amount_cents": 8000,
                       "payer_member_id": group.owner.member_id,
                       "splits": [{**part, "amount_cents": 4000} for part in draft["splits"]]}
    second = created(client, base, group.member, smaller)
    review(client, base, group.owner, second).raise_for_status()
    assert balances()[group.member.member_id] == 8000
    payment = {"request_id": str(uuid4()), "payer": group.owner.member_id,
               "payee": group.member.member_id, "amount": 8000}
    future = payment | {"paid_at": (api.clock.now + timedelta(days=1)).isoformat()}
    assert client.post(endpoint, headers=group.owner.headers, json=future).status_code == 422
    paid_at = (api.clock.now - timedelta(hours=1)).isoformat(timespec="microseconds")
    payment["paid_at"] = paid_at
    assert client.post(endpoint, headers=group.member.headers, json=payment).status_code == 403
    for _ in range(2):
        assert client.post(endpoint, headers=group.owner.headers, json=payment).status_code == 201
    assert balances()[group.member.member_id] == 0 and len(statement()["payments"]) == 1
    assert statement()["payments"][0]["paid_at"] == paid_at
    assert client.post(endpoint, headers=group.owner.headers,
                       json=payment | {"paid_at": None}).status_code == 409
    assert client.post(endpoint, headers=group.owner.headers, json=payment | {
        "request_id": str(uuid4())}).status_code == 409
    assert summary(client, base, group.owner)["expense_cents"] == 32000
    url = base + "/" + first["id"]
    proposal = client.post(url + "/changes", headers=group.member.headers,
                           json=correct_body(draft, first["revision"])).json()
    decide(client, url, group.owner, proposal).raise_for_status()
    assert balances()[group.member.member_id] == 1000
    personal = ledger + "/personal-statement"
    export = client.get(personal, headers=group.member.headers).json()
    assert export["member"]["remaining"] == 1000 and len(export["entries"]) == 2
    assert len(export["payments"]) == 1
    assert client.post(ledger + "/leave", headers=group.member.headers).is_success
    for path in ("personal-statement", "search", "logs", "settlements", "budget", "overview"):
        assert client.get(ledger + "/" + path, headers=group.member.headers).status_code == 403
    assert client.get(endpoint, headers=group.outsider.headers).status_code == 403
    assert statement()["members"][group.member.member_id]["status"] == "exited"
    assert balances()[group.member.member_id] == 1000
    current = read_detail(client, url, group.owner)["snapshot"]["record"]
    void = client.post(url + "/changes", headers=group.owner.headers, json={
        "kind": "void", "revision": current["revision"], "reason": "活动取消",
    }).json()
    decide(client, url, group.reviewer, void).raise_for_status()
    assert balances()[group.member.member_id] == -12000
    assert statement()["balance"] == -8000 and len(statement()["payments"]) == 1
    params = {"action": "change.approved", "actor_member_id": group.reviewer.member_id,
              "object_id": first["id"], "start": (api.clock.now-timedelta(hours=1)).isoformat(),
              "end": api.clock.now.isoformat()}
    logs = client.get(ledger + "/logs", headers=group.owner.headers, params=params).json()
    assert len(logs) == 1 and logs[0]["details"]["after"]["status"] == "voided"
    assert client.get(ledger + "/logs", headers=group.owner.headers,
                      params=params | {"object_id": second["id"]}).json() == []


def test_concurrent_payments_cannot_settle_one_debt_twice(entry_api, group):
    from concurrent.futures import ThreadPoolExecutor

    client, base, draft = entry_api
    entry = created(client, base, group.member, draft)
    review(client, base, group.owner, entry).raise_for_status()
    url = base.removesuffix("/entries") + "/settlements"

    def pay(_):
        body = {"request_id": str(uuid4()), "payer": group.owner.member_id,
                "payee": group.member.member_id, "amount": 12000}
        return client.post(url, headers=group.owner.headers, json=body).status_code

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(pay, range(2))) == [201, 409]
    data = client.get(url, headers=group.owner.headers).json()
    assert len(data["payments"]) == 1
    assert all(member["remaining"] == 0 for member in data["members"].values())


def test_large_category_subtotals_retain_integer_precision(entry_api, group):
    client, base, draft = entry_api
    amount = 2 ** 62
    for name in ("春季", "秋季"):
        entry = created(client, base, group.member, draft | {
            "request_id": str(uuid4()), "purpose": name, "amount_cents": amount,
            "splits": [{"member_id": group.member.member_id, "amount_cents": amount}],
            "duplicate_reason": "不同活动", "budget_reason": "超大整数边界测试",
        })
        review(client, base, group.owner, entry).raise_for_status()
    data = client.get(base.removesuffix("/entries") + "/search", headers=group.owner.headers).json()
    assert data["expense"] == 2 * amount == data["categories"]["餐饮"][1]
