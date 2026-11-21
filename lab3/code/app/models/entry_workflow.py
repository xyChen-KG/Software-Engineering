"""收支流程的输入、列表摘要和审核记录。"""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from app.models import EntryRecord, EntrySnapshot, EntryStatus, EntryType, RecordVersion
from app.models.changes import ChangeDetail
from app.models.entities import EntryContent
from app.models.receipts import ReceiptMeta


@dataclass(frozen=True, kw_only=True)
class EntryDraft(EntryContent):
    """表单数据由服务端关联账本、提交人及内容版本。"""

    duplicate_reason: str = ""

    def version(self, record: EntryRecord, number: int, now: datetime) -> RecordVersion:
        """建立经过金额和文本检查的内容快照。"""
        reason = self.no_receipt_reason.strip()
        return RecordVersion(
            entry_id=record.id, ledger_id=record.ledger_id, number=number,
            amount_cents=self.amount_cents, category=self.category.strip(),
            purpose=self.purpose.strip(), business_date=self.business_date,
            payer_member_id=self.payer_member_id, splits=self.splits,
            no_receipt_reason=reason, budget_reason=self.budget_reason.strip(), created_at=now,
        )


@dataclass(frozen=True, kw_only=True)
class Review:
    """一次审核对应一份内容版本，重新提交时旧审核仍可查。"""

    entry_id: UUID
    ledger_id: UUID
    version_number: int
    reviewer_member_id: UUID
    result: EntryStatus
    reason: str
    reviewed_at: datetime


@dataclass(frozen=True, kw_only=True)
class EntryRow:
    """收支列表显示当前版本及提交人。"""

    id: UUID
    kind: EntryType
    status: EntryStatus
    amount_cents: int
    category: str
    purpose: str
    business_date: date
    submitter: str


@dataclass(frozen=True, kw_only=True)
class Totals:
    """只累加正式记录的当前内容。"""

    income_cents: int
    expense_cents: int
    balance_cents: int


@dataclass(frozen=True, kw_only=True)
class EntryPage:
    """同一只读事务取得的列表与正式汇总。"""

    items: tuple[EntryRow, ...]
    summary: Totals
    next_offset: int | None


@dataclass(frozen=True, kw_only=True)
class EntryDetail:
    """当前收支、历史版本和审核时间线。"""

    snapshot: EntrySnapshot
    history: tuple[RecordVersion, ...]
    reviews: tuple[Review, ...]
    member_names: dict[UUID, str]
    other_approved_expense: int
    receipts: dict[int, tuple[ReceiptMeta, ...]]
    changes: tuple[ChangeDetail, ...]
