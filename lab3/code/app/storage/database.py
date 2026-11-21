"""创建独立 SQLite 连接并明确处理事务与资源释放。"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from app.errors import SchemaVersionError
from app.storage import schema
from app.storage.budget_alerts import BudgetAlerts
from app.storage.entry_workflow import CURRENT_CONTENT


class Database:
    """持有数据库文件位置；每次调用建立独立连接。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self, *, write: bool = True) -> Iterator[sqlite3.Connection]:
        """显式事务覆盖查询和 DDL；写事务提前取得写锁，失败整体回滚。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.row_factory = sqlite3.Row
            if not write:
                connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def check_connection(self) -> None:
        """只有结构已初始化且可查询，才报告数据库就绪。"""
        self.initialize()
        with self.connect(write=False) as connection:
            connection.execute("SELECT 1").fetchone()

    def initialize(self) -> int:
        """按版本前进迁移；不删除旧数据，任一步失败则整次迁移回滚。"""
        with self.connect(write=False) as connection:
            if self._check_schema(connection):
                return schema.SCHEMA_VERSION
        with self.connect() as connection:
            # 取得写锁后重查，避免两个启动进程同时建表。
            if not self._check_schema(connection):
                previous = connection.execute("PRAGMA user_version").fetchone()[0]
                for version in range(previous + 1, schema.SCHEMA_VERSION + 1):
                    for statement in schema.statements_for(version):
                        connection.execute(statement)
                    if version == 8 and previous:
                        self._seed_budget_alerts(connection)
                    connection.execute(f"PRAGMA user_version = {version}")
        return schema.SCHEMA_VERSION

    @staticmethod
    def _seed_budget_alerts(connection):
        """升级时为已有账本的当前预算状态补发一次，金额用整数分累计。"""
        alerts = BudgetAlerts(connection)
        when = datetime.now(UTC)
        for ledger_id, budget in connection.execute(
            "SELECT id, budget_cents FROM ledgers WHERE archived=0 AND budget_cents>0"
        ):
            rows = connection.execute("SELECT v.amount_cents " + CURRENT_CONTENT +
                                      " WHERE e.ledger_id=? AND e.kind='expense'"
                                      " AND e.status='approved'", (ledger_id,))
            spent = sum(row[0] for row in rows)
            alerts.publish(ledger_id, spent, budget, when)

    @staticmethod
    def _check_schema(connection: sqlite3.Connection) -> bool:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if version in range(1, schema.SCHEMA_VERSION + 1):
            if not schema.tables_for(version).issubset(tables):
                raise SchemaVersionError("数据库结构不完整，不能报告就绪")
            return version == schema.SCHEMA_VERSION
        if version != 0 or tables:
            raise SchemaVersionError("数据库版本不受支持，不会覆盖已有数据")
        return False
