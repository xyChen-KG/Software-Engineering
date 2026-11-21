"""操作日志的追加及条件查询；不提供改删接口。"""

from datetime import datetime
from uuid import UUID

from app.models import AuditEvent
from app.models.search import LogFilters
from app.models.validation import require_integer
from app.storage.common import Repository, encode_time


class AuditRepository(Repository):
    """业务层可以把日志与对应收支写入同一个事务。"""

    def append(self, event: AuditEvent) -> AuditEvent:
        """追加一条日志，数据库触发器同时禁止改删历史。"""
        self._execute(
            "INSERT INTO audit_events(id, ledger_id, actor_member_id, object_id, action, "
            "details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(event.id), str(event.ledger_id), str(event.actor_member_id), str(event.object_id),
             event.action, event.details, encode_time(event.created_at)),
        )
        return event

    def query(
        self, ledger_id: UUID, *, actor_member_id: UUID | None = None,
        action: str | None = None, limit: int = 100,
    ) -> tuple[AuditEvent, ...]:
        """支持按账本、操作成员和操作类型查询，按操作时间倒序。"""
        require_integer(limit, "查询条数", minimum=1)
        return self.filtered(ledger_id, LogFilters(actor_member_id=actor_member_id,
                                                   action=action, limit=limit))

    def filtered(self, ledger_id, filters):
        """按时间倒序，稳定分页。"""
        conditions = ["ledger_id = ?"]
        parameters = [str(ledger_id)]
        for clause, value in (("actor_member_id=?", filters.actor_member_id),
                              ("object_id=?", filters.object_id), ("action=?", filters.action),
                              ("created_at>=?", filters.start), ("created_at<=?", filters.end)):
            if value is not None:
                conditions.append(clause)
                parameters.append(encode_time(value) if isinstance(value, datetime) else str(value))
        rows = self._execute(
            "SELECT * FROM audit_events WHERE " + " AND ".join(conditions)
            + " ORDER BY created_at DESC, id LIMIT ? OFFSET ?",
            (*parameters, filters.limit, filters.offset),
        )
        return tuple(AuditEvent(
            id=UUID(row["id"]), ledger_id=UUID(row["ledger_id"]),
            actor_member_id=UUID(row["actor_member_id"]), object_id=UUID(row["object_id"]),
            action=row["action"], details=row["details"],
            created_at=datetime.fromisoformat(row["created_at"]),
        ) for row in rows)
