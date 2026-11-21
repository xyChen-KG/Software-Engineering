"""从环境变量读取服务、客户端和存储配置。"""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    """服务和桌面客户端共用的配置；不包含任何用户身份或权限。"""

    data_dir: Path
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_url: str = "http://127.0.0.1:8000"

    @property
    def database_path(self) -> Path:
        """返回 SQLite 文件路径，实际创建由存储层负责。"""
        return self.data_dir / "ledger.sqlite3"

    @classmethod
    def from_env(cls) -> "Settings":
        """读取配置，拒绝非法端口和无法用于连接的服务地址。"""
        try:
            port = int(os.environ.get("LEDGER_API_PORT", "8000"))
        except ValueError as exc:
            raise ValueError("LEDGER_API_PORT 必须是整数") from exc
        if not 1 <= port <= 65535:
            raise ValueError("LEDGER_API_PORT 必须在 1 到 65535 之间")

        host = os.environ.get("LEDGER_API_HOST", "127.0.0.1").strip()
        if not host:
            raise ValueError("LEDGER_API_HOST 不能为空")

        api_url = os.environ.get("LEDGER_API_URL", "http://127.0.0.1:8000").rstrip("/")
        parsed = urlsplit(api_url)
        try:
            url_port = parsed.port
        except ValueError as exc:
            raise ValueError("LEDGER_API_URL 中的端口不合法") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or url_port == 0:
            raise ValueError("LEDGER_API_URL 必须是有效的 http 或 https 服务地址")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("LEDGER_API_URL 不能包含账号信息")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("LEDGER_API_URL 不能包含路径、查询参数或片段")

        return cls(
            data_dir=Path(os.environ.get("LEDGER_DATA_DIR", "data")).expanduser().resolve(),
            api_host=host,
            api_port=port,
            api_url=api_url,
        )
