"""收支更正、作废申请与审核队列。"""

import json
from dataclasses import asdict
from datetime import date, datetime
from uuid import UUID

from app.errors import ConflictError
from app.models import RecordVersion, Split
from app.models.changes import ChangeKind, ChangeQueueRow, ChangeRequest, ChangeStatus
from app.storage.common import Repository, encode_time


def _decode_proposal(source: str | None) -> RecordVersion | None:
    if source is None:
        return None
    fields = json.loads(source)
    for key in ("entry_id", "ledger_id", "payer_member_id"):
        fields[key] = UUID(fields[key])
    fields["business_date"] = date.fromisoformat(fields["business_date"])
    fields["created_at"] = datetime.fromisoformat(fields["created_at"])
    fields["splits"] = tuple(
        Split(member_id=UUID(item["member_id"]), amount_cents=item["amount_cents"])
        for item in fields["splits"]
    )
    return RecordVersion(**fields)


def _read(row) -> ChangeRequest:
    return ChangeRequest(
        id=UUID(row["id"]), ledger_id=UUID(row["ledger_id"]), entry_id=UUID(row["entry_id"]),
        applicant_member_id=UUID(row["applicant_member_id"]), base_version=row["base_version"],
        base_revision=row["base_revision"], kind=ChangeKind(row["kind"]),
        reason=row["reason"], proposal=_decode_proposal(row["proposal"]),
        status=ChangeStatus(row["status"]), created_at=datetime.fromisoformat(row["created_at"]),
        reviewer_member_id=UUID(row["reviewer_member_id"]) if row["reviewer_member_id"] else None,
        review_reason=row["review_reason"],
        reviewed_at=datetime.fromisoformat(row["reviewed_at"]) if row["reviewed_at"] else None,
    )


class ChangeRepository(Repository):
    """待处理申请由唯一索引约束，一个申请只审核一次。"""

    def add(self, change: ChangeRequest) -> None:
        """保存申请及拟替换的内容，正式记录保持原状。"""
        proposal = json.dumps(asdict(change.proposal), ensure_ascii=False, default=str,
                              allow_nan=False) if change.proposal else None
        self._execute(
            "INSERT INTO entry_changes(id, ledger_id, entry_id, base_version, base_revision, "
            "applicant_member_id, kind, reason, proposal, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(change.id), str(change.ledger_id), str(change.entry_id), change.base_version,
             change.base_revision, str(change.applicant_member_id), change.kind.value,
             change.reason, proposal, encode_time(change.created_at)),
        )

    def get(self, entry_id: UUID, change_id: UUID) -> ChangeRequest:
        """通过记录和申请编号定位，不接受跨记录的申请。"""
        return _read(self._one(
            "SELECT * FROM entry_changes WHERE id=? AND entry_id=?",
            (str(change_id), str(entry_id)), "该记录中没有此变更申请",
        ))

    def pending(self, entry_id: UUID) -> bool:
        """检查记录是否已有待处理申请。"""
        return self._execute(
            "SELECT 1 FROM entry_changes WHERE entry_id=? AND status='pending'", (str(entry_id),),
        ).fetchone() is not None

    def history(self, entry_id: UUID) -> tuple[ChangeRequest, ...]:
        """最近申请优先，保留被驳回的草案和原因。"""
        return tuple(_read(row) for row in self._execute(
            "SELECT * FROM entry_changes WHERE entry_id=? ORDER BY created_at DESC, rowid DESC",
            (str(entry_id),),
        ))

    def queue(self, ledger_id: UUID, limit: int, offset: int) -> tuple[ChangeQueueRow, ...]:
        """按申请时间排序读取待处理条目。"""
        return tuple(
            ChangeQueueRow(id=UUID(row["id"]), entry_id=UUID(row["entry_id"]),
                           kind=ChangeKind(row["kind"]), purpose=row["purpose"],
                           reason=row["reason"], applicant_name=row["name"])
            for row in self._execute(
                "SELECT c.id, c.entry_id, c.kind, c.reason, v.purpose, u.name "
                "FROM entry_changes c "
                "JOIN entry_versions v ON v.entry_id=c.entry_id AND v.number=c.base_version "
                "JOIN memberships m ON m.id=c.applicant_member_id JOIN users u ON u.id=m.user_id "
                "WHERE c.ledger_id=? AND c.status='pending' "
                "ORDER BY c.created_at, c.id LIMIT ? OFFSET ?", (str(ledger_id), limit, offset),
            )
        )

    def resolve(self, change: ChangeRequest) -> None:
        """悄悄覆盖历史决定属于冲突。"""
        cursor = self._execute(
            "UPDATE entry_changes SET status=?, reviewer_member_id=?, review_reason=?, "
            "reviewed_at=? WHERE id=? AND status='pending'",
            (change.status.value, str(change.reviewer_member_id), change.review_reason,
             encode_time(change.reviewed_at), str(change.id)),
        )
        if cursor.rowcount != 1:
            raise ConflictError("申请已处理，请刷新后查看结果")
