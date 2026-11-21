"""向业务层提供共享事务，不让各仓储分别提交。"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from app.storage.audit import AuditRepository
from app.storage.budget_alerts import BudgetAlerts
from app.storage.accounts import AccountRepository, SessionRepository
from app.storage.catalog import LedgerRepository, UserRepository
from app.storage.changes import ChangeRepository
from app.storage.database import Database
from app.storage.entries import EntryRepository
from app.storage.entry_workflow import EntryWorkflowRepository
from app.storage.invitations import InvitationRepository
from app.storage.receipts import ReceiptRepository
from app.storage.reporting import ReportingRepository
from app.storage.settlements import SettlementRepository


class Repositories:  # pylint: disable=too-many-instance-attributes
    """这些仓储在一个工作单元内使用同一个 SQLite 连接。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.users = UserRepository(connection)
        self.ledgers = LedgerRepository(connection)
        self.entries = EntryRepository(connection)
        self.entry_workflow = EntryWorkflowRepository(connection)
        self.changes = ChangeRepository(connection)
        self.receipts = ReceiptRepository(connection)
        self.reporting = ReportingRepository(connection)
        self.settlements = SettlementRepository(connection)
        self.audit = AuditRepository(connection)
        self.budget_alerts = BudgetAlerts(connection)
        self.accounts = AccountRepository(connection)
        self.sessions = SessionRepository(connection)
        self.invitations = InvitationRepository(connection)


class LedgerStore:
    """持久化入口；上下文退出时统一提交，异常时全部回滚。"""

    def __init__(self, database: Database) -> None:
        self.database = database

    @contextmanager
    def transaction(self, *, write: bool = True) -> Iterator[Repositories]:
        """只读事务在 SQLite 层拒绝写入；写事务串行取得锁。"""
        self.database.initialize()
        with self.database.connect(write=write) as connection:
            yield Repositories(connection)
