"""对已通过记录的独立申请及审核结果。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.models import RecordVersion
from app.models.receipts import ReceiptMeta


class ChangeKind(StrEnum):
    """更正保留正式记录并切换版本，作废只更改状态。"""

    CORRECT = "correct"
    VOID = "void"


class ChangeStatus(StrEnum):
    """申请状态与正式记录状态分开。"""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True, kw_only=True)
class ChangeRequest:  # pylint: disable=too-many-instance-attributes
    """草案、基准及决定在同一申请下保存。"""

    id: UUID
    ledger_id: UUID
    entry_id: UUID
    base_version: int
    base_revision: int
    applicant_member_id: UUID
    kind: ChangeKind
    reason: str
    created_at: datetime
    proposal: RecordVersion | None = None
    status: ChangeStatus = ChangeStatus.PENDING
    reviewer_member_id: UUID | None = None
    review_reason: str = ""
    reviewed_at: datetime | None = None


@dataclass(frozen=True, kw_only=True)
class ChangeDetail:
    """申请及其凭证摘要。"""

    request: ChangeRequest
    receipts: tuple[ReceiptMeta, ...]


@dataclass(frozen=True, kw_only=True)
class ChangeQueueRow:
    """审核人用于定位申请的待处理条目。"""

    id: UUID
    entry_id: UUID
    kind: ChangeKind
    purpose: str
    reason: str
    applicant_name: str
