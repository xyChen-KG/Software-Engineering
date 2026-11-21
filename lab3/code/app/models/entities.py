"""与 UML 核心类对应的不可变对象；不依赖界面或 SQLite。"""

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from app.errors import ValidationError
from app.models.enums import EntryStatus, EntryType, MembershipStatus, Role
from app.models.validation import (
    require_enum,
    require_integer,
    require_text,
    require_time,
    require_uuid,
)


def utc_now() -> datetime:
    """生成带时区的 UTC 操作时间。"""
    return datetime.now(UTC)


@dataclass(frozen=True, kw_only=True)
class User:
    """用户身份资料；密码与登录机制在后续认证模块实现。"""

    name: str
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        require_uuid(self.id, "用户编号")
        require_text(self.name, "用户姓名")
        require_time(self.created_at, "创建时间")


@dataclass(frozen=True, kw_only=True)
class Ledger:
    """活动账本；预算为 None 表示未设置，零预算也合法。"""

    name: str
    id: UUID = field(default_factory=uuid4)
    budget_cents: int | None = None
    archived: bool = False
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        require_uuid(self.id, "账本编号")
        require_text(self.name, "账本名称")
        if self.budget_cents is not None:
            require_integer(self.budget_cents, "预算分")
        if not isinstance(self.archived, bool):
            raise ValidationError("归档标志必须是布尔值")
        require_time(self.created_at, "创建时间")


@dataclass(frozen=True, kw_only=True)
class Membership:
    """用户与账本的关联，包含该账本中的角色及加入状态。"""

    user_id: UUID
    ledger_id: UUID
    role: Role = Role.MEMBER
    status: MembershipStatus = MembershipStatus.ACTIVE
    id: UUID = field(default_factory=uuid4)
    joined_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for name in ("id", "user_id", "ledger_id"):
            require_uuid(getattr(self, name), name)
        require_enum(self.role, Role, "成员角色")
        require_enum(self.status, MembershipStatus, "成员状态")
        require_time(self.joined_at, "加入时间")


@dataclass(frozen=True, kw_only=True)
class Split:
    """某个账本成员在一个内容版本中的承担额，允许个人承担零元。"""

    member_id: UUID
    amount_cents: int

    def __post_init__(self) -> None:
        require_uuid(self.member_id, "承担成员编号")
        require_integer(self.amount_cents, "分摊金额分")


@dataclass(frozen=True, kw_only=True)
class EntryContent:
    """输入草稿与存档版本共用的收支字段。"""

    amount_cents: int
    category: str
    purpose: str
    business_date: date
    payer_member_id: UUID
    splits: tuple[Split, ...] = ()
    no_receipt_reason: str = ""
    budget_reason: str = ""


@dataclass(frozen=True, kw_only=True)
class RecordVersion(EntryContent):
    """收支内容快照；修改生成新版本，不覆盖旧版本及其分摊。"""

    entry_id: UUID
    ledger_id: UUID
    number: int
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for name in ("entry_id", "ledger_id", "payer_member_id"):
            require_uuid(getattr(self, name), name)
        require_integer(self.number, "内容版本号", minimum=1)
        require_integer(self.amount_cents, "金额分", minimum=1)
        require_text(self.category, "类别")
        require_text(self.purpose, "用途")
        require_text(self.no_receipt_reason, "无凭证说明", allow_empty=True)
        if not isinstance(self.business_date, date) or isinstance(self.business_date, datetime):
            raise ValidationError("业务日期必须是 date，不包含操作时间")
        if not isinstance(self.splits, tuple) or not all(
            isinstance(split, Split) for split in self.splits
        ):
            raise ValidationError("分摊必须是 Split 对象组成的元组")
        if len({split.member_id for split in self.splits}) != len(self.splits):
            raise ValidationError("同一版本不能重复填写同一成员的分摊")
        # 分摊顺序不表达业务含义，统一排序后可稳定比较输入与持久化结果。
        ordered_splits = tuple(sorted(self.splits, key=lambda split: split.member_id))
        object.__setattr__(self, "splits", ordered_splits)
        require_time(self.created_at, "版本创建时间")


@dataclass(frozen=True, kw_only=True)
class EntryRecord:
    """保持固定业务编号的收支头信息，内容版本与并发版本分别保存。"""

    ledger_id: UUID
    submitter_member_id: UUID
    kind: EntryType
    request_id: UUID
    id: UUID = field(default_factory=uuid4)
    status: EntryStatus = EntryStatus.PENDING
    current_version: int = 1
    revision: int = 1
    original_expense_id: UUID | None = None
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for name in ("id", "ledger_id", "submitter_member_id", "request_id"):
            require_uuid(getattr(self, name), name)
        require_enum(self.kind, EntryType, "收支类型")
        require_enum(self.status, EntryStatus, "记录状态")
        require_integer(self.current_version, "当前内容版本", minimum=1)
        require_integer(self.revision, "并发版本", minimum=1)
        if self.original_expense_id is not None:
            require_uuid(self.original_expense_id, "原支出编号")
            if self.kind != EntryType.INCOME or self.original_expense_id == self.id:
                raise ValidationError("退款必须是关联另一笔支出的收入")
        require_time(self.created_at, "记录创建时间")


@dataclass(frozen=True, kw_only=True)
class EntrySnapshot:
    """将头信息与其当前内容组合，并校验分摊及版本一致性。"""

    record: EntryRecord
    content: RecordVersion

    def __post_init__(self) -> None:
        if (
            self.record.id != self.content.entry_id
            or self.record.ledger_id != self.content.ledger_id
            or self.record.current_version != self.content.number
        ):
            raise ValidationError("收支头信息与内容版本不一致")
        if self.record.kind == EntryType.EXPENSE:
            if not self.content.splits:
                raise ValidationError("支出必须填写分摊")
            total = sum(split.amount_cents for split in self.content.splits)
            if total != self.content.amount_cents:
                raise ValidationError("支出分摊合计必须等于支出金额")
        elif self.content.splits:
            raise ValidationError("收入不填写支出分摊")


@dataclass(frozen=True, kw_only=True)
class AuditEvent:
    """不可变的操作日志，详情使用 JSON 对象文本保存前后内容及原因。"""

    ledger_id: UUID
    actor_member_id: UUID
    object_id: UUID
    action: str
    details: str = "{}"
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for name in ("id", "ledger_id", "actor_member_id", "object_id"):
            require_uuid(getattr(self, name), name)
        require_text(self.action, "操作类型")
        require_text(self.details, "日志详情")
        try:
            details = json.loads(self.details)
            json.dumps(details, allow_nan=False)
        except ValueError as exc:
            raise ValidationError("日志详情必须是合法 JSON") from exc
        if not isinstance(details, dict):
            raise ValidationError("日志详情必须是 JSON 对象")
        require_time(self.created_at, "操作时间")
