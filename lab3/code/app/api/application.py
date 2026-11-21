"""FastAPI 应用工厂和初始健康检查接口。"""

import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request, Response

from app import __version__
from app.config import Settings
from app.api.auth_routes import auth_router
from app.api.change_routes import change_router
from app.api.entry_routes import entry_router
from app.api.error_handlers import install_error_handlers
from app.api.ledger_routes import ledger_router
from app.api.report_routes import report_router
from app.errors import SchemaVersionError
from app.models.entities import utc_now
from app.services.auth import AuthService
from app.services.changes import ChangeService
from app.services.entries import EntryService
from app.services.ledgers import LedgerService
from app.storage.database import Database
from app.storage.store import LedgerStore

LOGGER = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None, *, clock: Callable[[], datetime] = utc_now,
) -> FastAPI:
    """为每个服务实例创建配置和存储对象，避免导入时写入数据库。"""
    config = settings if settings is not None else Settings.from_env()
    database = Database(config.database_path)
    application = FastAPI(title="多人共享记账系统", version=__version__)
    store = LedgerStore(database)
    auth = AuthService(store, clock=clock)
    ledgers = LedgerService(store, auth)
    application.state.store = store
    application.state.auth = auth
    application.state.ledgers = ledgers
    install_error_handlers(application)
    application.include_router(auth_router(auth))
    application.include_router(ledger_router(ledgers))
    application.include_router(report_router(ledgers))
    entries = EntryService(ledgers)
    application.include_router(entry_router(entries))
    application.include_router(change_router(ChangeService(entries)))

    @application.middleware("http")
    async def private_response(request: Request, call_next) -> Response:
        """账号与账本响应不由浏览器缓存，以免退出后继续复用旧响应。"""
        response = await call_next(request)
        if request.url.path.startswith(("/auth/", "/ledgers", "/invitations", "/me/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.get("/health", tags=["运行检查"])
    def health() -> dict[str, str]:
        """仅当 SQLite 可实际访问时报告服务已就绪。"""
        try:
            database.check_connection()
        except (OSError, sqlite3.Error, SchemaVersionError) as exc:
            LOGGER.exception("数据库就绪检查失败")
            raise HTTPException(status_code=503, detail="数据存储暂时不可用") from exc
        return {"status": "ok", "version": __version__, "database": "ready"}

    return application
