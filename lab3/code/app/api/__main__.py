"""后端启动入口：python -m app.api 或 ledger-server。"""

import uvicorn

from app.api.application import create_app
from app.config import Settings


def main() -> None:
    """默认仅监听本机，供桌面客户端访问。"""
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
