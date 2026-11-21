"""与实验二角色及状态图对应的枚举；角色属于账本成员关系。"""

from enum import StrEnum


class Role(StrEnum):
    """同一用户在不同账本中可以承担不同角色。"""

    OWNER = "owner"
    REVIEWER = "reviewer"
    MEMBER = "member"


class MembershipStatus(StrEnum):
    """退出后保留原成员关系供历史账目引用。"""

    ACTIVE = "active"
    EXITED = "exited"


class EntryType(StrEnum):
    """金额始终为正数，用类型区分收入和支出。"""

    INCOME = "income"
    EXPENSE = "expense"


class EntryStatus(StrEnum):
    """收支记录的五个已定义状态。"""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    VOIDED = "voided"
