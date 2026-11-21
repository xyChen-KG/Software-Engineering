"""仓储共用的 SQL 错误转换、时间编码和操作级保存点。"""

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from app.errors import ConflictError, NotFoundError


def encode_time(value: datetime) -> str:
    """统一存 UTC，保证不同客户端时区下的操作时间可以正确排序。"""
    return value.astimezone(UTC).isoformat(timespec="microseconds")


class Repository:
    """仓储只使用调用方提供的连接，不自行提交业务事务。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def _execute(self, sql: str, parameters: Sequence = ()) -> sqlite3.Cursor:
        try:
            return self._connection.execute(sql, parameters)
        except sqlite3.IntegrityError as exc:
            raise ConflictError("数据唯一性或关联约束冲突") from exc

    def _one(self, sql: str, parameters: Sequence, message: str) -> sqlite3.Row:
        row = self._execute(sql, parameters).fetchone()
        if row is None:
            raise NotFoundError(message)
        return row

    @contextmanager
    def _atomic(self) -> Iterator[None]:
        """即使调用方在外层事务内捕获异常，单次多表操作也不会留下半成品。"""
        name = f"operation_{uuid4().hex}"
        self._connection.execute(f"SAVEPOINT {name}")
        try:
            yield
            self._connection.execute(f"RELEASE SAVEPOINT {name}")
        except Exception:
            self._connection.execute(f"ROLLBACK TO SAVEPOINT {name}")
            self._connection.execute(f"RELEASE SAVEPOINT {name}")
            raise
