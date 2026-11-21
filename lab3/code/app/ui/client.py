"""Qt 的异步 HTTP 客户端。登录令牌由窗口保存在内存中。"""

import json
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QByteArray, QObject, QTimer, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


@dataclass(frozen=True)
class Reply:
    """一次 HTTP 调用的结果，网络错误的状态码为零。"""

    status: int
    data: dict | list | None
    message: str = ""
    code: str = ""

    @property
    def ok(self) -> bool:
        """只有格式有效且成功的响应才交给页面。"""
        return 200 <= self.status < 300 and not self.message


def parse_reply(status: int, raw: bytes) -> Reply:
    """只取服务端的业务提示，不在界面显示底层异常或请求体。"""
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        data = None
    if status == 204 or (200 <= status < 300 and isinstance(data, (dict, list))):
        return Reply(status, data)

    messages = {
        0: "无法连接记账服务，请确认服务已启动后重试。",
        422: "填写内容不符合要求，请检查后重试。",
        429: "尝试次数过多，请稍后再试。",
    }
    if isinstance(data, dict) and isinstance(data.get("detail"), str) and 400 <= status < 500:
        message = data["detail"]
    else:
        message = messages.get(status, "服务暂时无法完成操作，请稍后重试。")
    code = data.get("code", "") if isinstance(data, dict) else ""
    return Reply(status, None, message, code)


class ApiClient(QObject):
    """所有请求使用配置中的同一个服务，禁止请求重定向。"""

    def __init__(self, base_url: str, parent: QObject) -> None:
        super().__init__(parent)
        self.base_url = base_url
        self.network = QNetworkAccessManager(self)
        self.network.setRedirectPolicy(QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)

    def send(
        self, method: str, path: str, done: Callable[[Reply], None], *,
        token: str = "", body: dict | None = None,
    ) -> None:
        """在 Qt 事件循环发起调用，总时限到达时中止请求。"""
        request = QNetworkRequest(QUrl(self.base_url + path))
        request.setRawHeader(b"Accept", b"application/json")
        if token:
            request.setRawHeader(b"Authorization", b"Bearer " + token.encode("ascii"))
        payload = QByteArray()
        if body is not None:
            request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
            payload = QByteArray(json.dumps(body).encode("utf-8"))
        reply = self.network.sendCustomRequest(request, method.encode("ascii"), payload)
        timer = QTimer(reply)
        timer.setSingleShot(True)
        timer.timeout.connect(reply.abort)  # pylint: disable=no-member
        timer.start(8000)
        reply.finished.connect(lambda: self._finish(reply, timer, done))

    @staticmethod
    def _finish(reply: QNetworkReply, timer: QTimer, done: Callable[[Reply], None]) -> None:
        timer.stop()
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) or 0
        raw = bytes(reply.readAll()) if reply.isReadable() else b""
        if reply.error() != QNetworkReply.NetworkError.NoError and status < 400:
            status = 0
        reply.deleteLater()
        done(parse_reply(status, raw))
