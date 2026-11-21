"""使用真实 HTTP、20 个不同账号和临时万笔账本验证 REQ-014。"""

import argparse
import csv
import json
import math
import secrets
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Barrier
from time import perf_counter
from urllib.parse import urlencode
from uuid import uuid4

from api_smoke import LocalAPI
from smoke_check import running_backend

from app.storage.database import Database


def participants(api):
    """每位测试者通过注册、登录、邀请加入同一个账本。"""
    actors = []
    path = ""
    for index in range(20):
        username = f"bench_{index}"
        password = "perf-" + secrets.token_urlsafe(20)
        credentials = {"username": username, "password": password}
        api.request("POST", "/auth/register", payload={**credentials, "name": username},
                    expected=201)
        token = api.request("POST", "/auth/login", payload=credentials)["access_token"]
        if index == 0:
            ledger = api.request("POST", "/ledgers", token=token,
                                 payload={"name": "临时万笔账本"}, expected=201)
            path = f"/ledgers/{ledger['id']}"
            member = ledger["member_id"]
        else:
            invite = api.request("POST", path + "/invitations", token=actors[0][0],
                                 payload={"username": username}, expected=201)
            member = api.request("POST", f"/invitations/{invite['id']}/accept", token=token)["id"]
        actors.append((token, member))
    return path, actors


def seed(database, ledger, actors):
    """批量构造可校验的历史版本、分摊和日志，不计入查询耗时。"""
    old = datetime.now(UTC) - timedelta(days=4 * 366)
    stamp = old.isoformat()
    cases = []
    with database.connect() as connection:
        for index in range(10000):
            member = actors[index % len(actors)][1]
            kind = "income" if index % 5 == 0 else "expense"
            status = "pending" if index % 7 == 0 else "approved"
            category = f"类别{index % 4}"
            day = (old.date() + timedelta(days=index % 90)).isoformat()
            key, amount = str(uuid4()), 1000 + index
            connection.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?)",
                               (key, ledger, member, kind, str(uuid4()), status, 1, 1, None, stamp))
            connection.execute("INSERT INTO entry_versions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               (key, ledger, 1, amount, category, f"活动采购 {index}", day,
                                member, "测试凭据说明", stamp, ""))
            if kind == "expense":
                connection.execute("INSERT INTO splits VALUES (?,?,?,?,?)",
                                   (key, 1, ledger, member, amount))
            if index == 0:
                connection.execute("INSERT INTO audit_events VALUES (?,?,?,?,?,?,?)", (
                    str(uuid4()), ledger, member, key, "entry.submitted", "{}", stamp))
            cases.append((key, member, kind, status, category, day, amount))
    return cases


def sample_queries(cases, member):
    """逐组预先计算期望金额，用于排除只返回空结果的假通过。"""
    cutoff = (date.fromisoformat(cases[0][5]) + timedelta(days=45)).isoformat()
    patterns = [
        ({}, lambda r: True),
        ({"member_id": member}, lambda r: r[1] == member),
        ({"kind": "expense", "category": "类别1"}, lambda r: r[2] == "expense" and r[4] == "类别1"),
        ({"start": cutoff, "minimum": 5000}, lambda r: r[5] >= cutoff and r[6] >= 5000),
        ({"keyword": "采购", "maximum": 7000, "status": "approved"}, lambda r: r[6] <= 7000),
    ]
    samples = []
    for query, matches in patterns:
        approved = [r for r in cases if r[3] == "approved" and matches(r)]
        income = sum(r[6] for r in approved if r[2] == "income")
        expense = sum(r[6] for r in approved if r[2] == "expense")
        samples.append((urlencode(query), income, expense))
    return samples


def measure(url, path, actors, cases, output):
    """五轮同时释放 20 个客户端，超时和异常计为失败。"""
    gate = Barrier(20)
    def worker(actor):
        api = LocalAPI(url)
        samples = sample_queries(cases, actor[1])
        times = []
        for query, income, expense in samples:
            gate.wait(timeout=30)
            start = perf_counter()
            try:
                data = api.request("GET", path + "/search?" + query, token=actor[0])
                valid = (data["income"], data["expense"]) == (income, expense) and bool(
                    data["items"])
            except (RuntimeError, OSError, ValueError):
                valid = False
            times.append((perf_counter() - start, valid))
        return times

    with ThreadPoolExecutor(max_workers=20) as pool:
        groups = list(pool.map(worker, actors))
    if output:
        with output.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["client", "round", "seconds", "correct"])
            for index, group in enumerate(groups, 1):
                writer.writerows((index, turn, f"{value:.6f}", valid)
                                 for turn, (value, valid) in enumerate(group, 1))
    results = [sample for group in groups for sample in group]
    durations = sorted(value for value, _ in results)
    return {"requests": len(results), "correct": sum(valid for _, valid in results),
            "within_2s": sum(value <= 2 and valid for value, valid in results),
            "median_s": round(statistics.median(durations), 4),
            "p95_s": round(durations[math.ceil(.95 * len(results)) - 1], 4),
            "max_s": round(max(durations), 4)}


def main():
    """验证旧账、访问控制与端到端查询性能。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=Path)
    output = parser.parse_args().samples
    with running_backend() as (url, env):
        api = LocalAPI(url)
        path, actors = participants(api)
        cases = seed(Database(Path(env["LEDGER_DATA_DIR"]) / "ledger.sqlite3"),
                     path.split("/")[-1], actors)
        key = cases[0][0]
        detail = api.request("GET", path + "/entries/" + key, token=actors[1][0])["detail"]
        recorded = datetime.fromisoformat(detail["history"][0]["created_at"])
        assert datetime.now(UTC) - recorded >= timedelta(days=3 * 366)
        logs = api.request("GET", path + "/logs?object_id=" + key, token=actors[1][0])
        assert len(logs) == 1
        result = measure(url, path, actors, cases, output)
        print(json.dumps({"users": 20, "entries": len(cases), **result}, ensure_ascii=False))
        assert result["correct"] == 100 and result["within_2s"] >= 95


if __name__ == "__main__":
    main()
