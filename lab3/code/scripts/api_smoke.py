"""用真实 HTTP 进程验证账号、邀请和账本权限，所有数据均临时生成。"""

import json
import secrets
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from smoke_check import running_backend


class LocalAPI:
    """仅连接检查脚本生成的本机服务，错误信息不打印请求体或令牌。"""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.opener = build_opener(ProxyHandler({}))

    def request(
        self, method: str, path: str, *, token: str | None = None,
        payload: dict | None = None, expected: int = 200,
    ):
        """发送 JSON 请求并验证状态码。"""
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        request = Request(self.base_url + path, method=method, headers=headers,
                          data=json.dumps(payload).encode("utf-8") if payload is not None else None)
        try:
            with self.opener.open(request, timeout=10) as response:
                status, data = response.status, response.read()
        except HTTPError as error:
            status, data = error.code, error.read()
        if status != expected:
            raise RuntimeError(f"{method} {path}：预期 {expected}，实际 {status}")
        return json.loads(data) if data else None


def run_flow(api: LocalAPI) -> None:
    """发起真实注册登录，不复用生产账号、固定令牌或真实账本。"""
    password = "course-" + secrets.token_urlsafe(24)
    tokens = {}
    for username in ("test_owner", "test_member"):
        api.request("POST", "/auth/register", payload={
            "username": username, "name": username, "password": password,
        }, expected=201)
        tokens[username] = api.request("POST", "/auth/login", payload={
            "username": username, "password": password,
        })["access_token"]
    owner, member = tokens["test_owner"], tokens["test_member"]
    ledger = api.request("POST", "/ledgers", token=owner,
                         payload={"name": "临时联通检查", "budget_cents": 10000}, expected=201)
    base = f"/ledgers/{ledger['id']}"
    invitation = api.request("POST", base + "/invitations", token=owner,
                             payload={"username": "test_member"}, expected=201)
    joined = api.request("POST", f"/invitations/{invitation['id']}/accept", token=member)
    api.request("GET", base, token=member)
    api.request("PATCH", base, token=member, payload={"name": "越权修改"}, expected=403)
    api.request("DELETE", base + f"/members/{joined['id']}", token=owner)
    api.request("GET", base, token=member, expected=403)
    api.request("POST", "/auth/logout", token=owner, expected=204)
    api.request("GET", "/ledgers", token=owner, expected=401)
    print("PASS：注册、登录、创建账本、邀请接受、越权拒绝、移除失权及退出登录。")


def main() -> None:
    """测试完成后自动关闭服务并删除临时数据库。"""
    with running_backend() as (base_url, _):
        run_flow(LocalAPI(base_url))


if __name__ == "__main__":
    main()
