"""认证和邀请的内部数据对象；密码散列与令牌摘要不用于 API 序列化。"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from app.models.entities import utc_now
from app.models.enums import Role


@dataclass(frozen=True, kw_only=True)
class Account:
    """用户的登录凭据与失败计数，密码散列不出现在 repr 中。"""

    user_id: UUID
    username: str
    password_hash: str = field(repr=False)
    failed_attempts: int = 0
    locked_until: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, kw_only=True)
class Session:
    """只存随机令牌的 SHA-256 摘要，不存客户端持有的明文令牌。"""

    token_hash: str = field(repr=False)
    user_id: UUID
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None


@dataclass(frozen=True, kw_only=True)
class Invitation:
    """邀请绑定具体用户及账本，接受时仍须校验当前权限与状态。"""

    ledger_id: UUID
    target_user_id: UUID
    inviter_member_id: UUID
    role: Role
    expires_at: datetime
    id: UUID = field(default_factory=uuid4)
    status: str = "pending"
    created_at: datetime = field(default_factory=utc_now)
    resolved_at: datetime | None = None
