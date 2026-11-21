"""明确 HTTP 输入输出边界，避免将密码散列或内部会话对象序列化。"""

import json
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, StringConstraints
from pydantic import model_validator

from app.models import AuditEvent, MembershipStatus, Role
from app.models.validation import MAX_INTEGER
from app.services.auth import Profile
from app.services.ledgers import InvitationInfo, LedgerAccess, MemberInfo

Username = Annotated[str, StringConstraints(strict=True, strip_whitespace=True,
                                           pattern=r"^[A-Za-z0-9_]{3,32}$")]
Name = Annotated[str, StringConstraints(strict=True, strip_whitespace=True,
                                       min_length=1, max_length=100)]
Budget = Annotated[int, Field(strict=True, ge=0, le=MAX_INTEGER)]
AssignableRole = Literal[Role.MEMBER, Role.REVIEWER]
PermissionReason = Annotated[str, StringConstraints(strict=True, strip_whitespace=True,
                                                    min_length=1, max_length=500)]


class RequestModel(BaseModel):
    """拒绝 user_id、owner 或任意多余字段，避免批量赋值越权。"""

    model_config = ConfigDict(extra="forbid")


class RegisterRequest(RequestModel):
    """注册资料；密码不在模型 repr 中显示。"""

    username: Username
    name: Name
    password: SecretStr = Field(min_length=15, max_length=128)


class LoginRequest(RequestModel):
    """登录失败统一由认证服务处理，长度上限限制资源消耗。"""

    username: Username
    password: SecretStr = Field(min_length=1, max_length=128)


class LedgerCreate(RequestModel):
    """负责人来自当前会话，不是客户端字段。"""

    name: Name
    budget_cents: Budget | None = None


class LedgerUpdate(RequestModel):
    """区分省略预算与显式清空预算，拒绝名称或归档标志为 null。"""

    name: Name | None = None
    budget_cents: Budget | None = None
    archived: StrictBool | None = None

    @model_validator(mode="after")
    def validate_patch(self):
        """没有字段时不生成空更新日志。"""
        if not self.model_fields_set:
            raise ValueError("至少提供一个账本设置字段")
        if any(field in self.model_fields_set and getattr(self, field) is None
               for field in ("name", "archived")):
            raise ValueError("名称和归档状态不能为 null")
        return self


class InviteRequest(RequestModel):
    """只能邀请普通成员或审核员。"""

    username: Username
    role: AssignableRole = Role.MEMBER


class RoleRequest(RequestModel):
    """负责人变更有独立原子操作。"""

    role: AssignableRole
    reason: PermissionReason


class TransferRequest(RequestModel):
    """指定另一位已在册成员接任。"""

    member_id: UUID
    reason: PermissionReason


class ProfileResponse(BaseModel):
    """安全公开的账号信息。"""

    id: UUID
    name: str
    username: str
    created_at: datetime

    @classmethod
    def from_profile(cls, profile: Profile):
        """显式选取用户字段，不返回 Account 数据对象。"""
        return cls(id=profile.user.id, name=profile.user.name, username=profile.username,
                   created_at=profile.user.created_at)


class TokenResponse(BaseModel):
    """仅登录响应包含新令牌。"""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: ProfileResponse


class LedgerResponse(BaseModel):
    """同时返回当前身份，客户端只用于展示，后端仍重新校验。"""

    id: UUID
    name: str
    budget_cents: int | None
    archived: bool
    created_at: datetime
    member_id: UUID
    role: Role

    @classmethod
    def from_access(cls, access: LedgerAccess):
        """选择账本设置及当前成员身份。"""
        return cls(id=access.ledger.id, name=access.ledger.name,
                   budget_cents=access.ledger.budget_cents, archived=access.ledger.archived,
                   created_at=access.ledger.created_at, member_id=access.membership.id,
                   role=access.membership.role)


class MemberResponse(BaseModel):
    """不包含密码、会话或账号失败次数。"""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID
    ledger_id: UUID
    role: Role
    status: MembershipStatus
    joined_at: datetime


class NamedMemberResponse(MemberResponse):
    """共享成员列表包含显示姓名。"""

    name: str

    @classmethod
    def from_info(cls, info: MemberInfo):
        """领域对象与显示信息分开组合。"""
        return cls(**MemberResponse.model_validate(info.membership).model_dump(), name=info.name)


class InvitationResponse(BaseModel):
    """邀请标识本身不是授权凭据，接受时仍验证目标会话。"""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ledger_id: UUID
    target_user_id: UUID
    inviter_member_id: UUID
    role: Role
    status: str
    created_at: datetime
    expires_at: datetime
    resolved_at: datetime | None


class InvitationInboxResponse(InvitationResponse):
    """仅目标账号能读取的邀请上下文。"""

    ledger_name: str
    inviter_name: str

    @classmethod
    def from_info(cls, info: InvitationInfo):
        """不要求用户加入后才能知道账本名称。"""
        data = InvitationResponse.model_validate(info.invitation).model_dump()
        return cls(**data, ledger_name=info.ledger_name, inviter_name=info.inviter_name)


class AuditResponse(BaseModel):
    """日志详情解析为 JSON 对象。"""

    id: UUID
    ledger_id: UUID
    actor_member_id: UUID
    object_id: UUID
    action: str
    details: dict
    created_at: datetime

    @classmethod
    def from_event(cls, event: AuditEvent):
        """只返回业务日志字段。"""
        return cls(id=event.id, ledger_id=event.ledger_id,
                   actor_member_id=event.actor_member_id,
                   object_id=event.object_id, action=event.action,
                   details=json.loads(event.details),
                   created_at=event.created_at)
