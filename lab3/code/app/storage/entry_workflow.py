"""收支列表、正式汇总与审核历史的持久化操作。"""

from datetime import date, datetime
from uuid import UUID

from app.models import EntryStatus, EntryType
from app.models.entry_workflow import EntryPage, EntryRow, Review, Totals
from app.storage.common import Repository, encode_time

CURRENT_CONTENT = """
    FROM entries e JOIN entry_versions v
    ON v.entry_id=e.id AND v.number=e.current_version
"""


class EntryWorkflowRepository(Repository):
    """使用已有版本表，正式汇总随审核状态和当前版本更新。"""

    def list_page(
        self, ledger_id: UUID, status: EntryStatus | None, limit: int, offset: int,
    ) -> EntryPage:
        """用一个连接读取分页列表和整本账本的正式汇总。"""
        where = "WHERE e.ledger_id=?"
        parameters = [str(ledger_id)]
        if status is not None:
            where += " AND e.status=?"
            parameters.append(status.value)
        rows = self._execute(
            "SELECT e.id, e.kind, e.status, v.amount_cents, v.category, v.purpose, "
            "v.business_date, u.name AS submitter " + CURRENT_CONTENT
            + "JOIN memberships m ON e.submitter_member_id=m.id "
            "JOIN users u ON u.id=m.user_id " + where
            + " ORDER BY v.business_date DESC, e.created_at DESC, e.id LIMIT ? OFFSET ?",
            (*parameters, limit + 1, offset),
        ).fetchall()
        items = tuple(
            EntryRow(
                id=UUID(row["id"]), kind=EntryType(row["kind"]), status=EntryStatus(row["status"]),
                amount_cents=row["amount_cents"], category=row["category"], purpose=row["purpose"],
                business_date=date.fromisoformat(row["business_date"]), submitter=row["submitter"],
            )
            for row in rows[:limit]
        )
        return EntryPage(items=items, summary=self.totals(ledger_id),
                         next_offset=offset + limit if len(rows) > limit else None)

    def totals(self, ledger_id: UUID) -> Totals:
        """用整数分累计，避免多笔账的总额溢出 SQLite 整数。"""
        income = expense = 0
        for row in self._execute(
            "SELECT e.kind, v.amount_cents " + CURRENT_CONTENT
            + "WHERE e.ledger_id=? AND e.status='approved'",
            (str(ledger_id),),
        ):
            if row["kind"] == EntryType.INCOME:
                income += row["amount_cents"]
            else:
                expense += row["amount_cents"]
        return Totals(income_cents=income, expense_cents=expense, balance_cents=income - expense)

    def save_review(self, review: Review) -> None:
        """与待审状态的处理结果在同一个工作单元保存。"""
        self._execute(
            "INSERT INTO entry_reviews(entry_id, version_number, ledger_id, reviewer_member_id, "
            "result, reason, reviewed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(review.entry_id), review.version_number, str(review.ledger_id),
             str(review.reviewer_member_id), review.result.value, review.reason,
             encode_time(review.reviewed_at)),
        )

    def reviews(self, entry_id: UUID) -> tuple[Review, ...]:
        """按内容版本顺序读取所有审核结果。"""
        return tuple(
            Review(
                entry_id=entry_id, ledger_id=UUID(row["ledger_id"]),
                version_number=row["version_number"],
                reviewer_member_id=UUID(row["reviewer_member_id"]),
                result=EntryStatus(row["result"]), reason=row["reason"],
                reviewed_at=datetime.fromisoformat(row["reviewed_at"]),
            )
            for row in self._execute(
                "SELECT * FROM entry_reviews WHERE entry_id=? ORDER BY version_number",
                (str(entry_id),),
            )
        )
