"""账本与成员业务；认证、对象权限、状态变更和日志处于同一事务。"""

import json
from dataclasses import dataclass, replace
from datetime import timedelta
from uuid import UUID

from app.errors import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from app.models import AuditEvent, Ledger, Membership, MembershipStatus, Role
from app.models.search import LogFilters
from app.models.validation import require_text
from app.models.accounts import Invitation
from app.services.auth import AuthService, normalize_username
from app.storage.store import LedgerStore, Repositories

INVITATION_DURATION = timedelta(days=7)


@dataclass(frozen=True, kw_only=True)
class LedgerAccess:
    """账本信息与当前用户在其中的成员身份。"""

    ledger: Ledger
    membership: Membership


@dataclass(frozen=True, kw_only=True)
class MemberInfo:
    """账本内可见的成员资料。"""

    membership: Membership
    name: str


@dataclass(frozen=True, kw_only=True)
class InvitationInfo:
    """接受前即可知道邀请来自哪个账本和哪位负责人。"""

    invitation: Invitation
    ledger_name: str
    inviter_name: str


class LedgerService:
    """服务入口接收会话令牌，身份始终由服务端数据库解析。"""

    def __init__(self, store: LedgerStore, auth: AuthService) -> None:
        self.store = store
        self.auth = auth

    def scope(
        self, repositories: Repositories, token: str, ledger_id: UUID, *, owner: bool = False,
    ) -> LedgerAccess:
        """在调用方事务中核对会话和当前账本成员身份。"""
        user = self.auth.authenticate(repositories, token)
        ledger = repositories.ledgers.get(ledger_id)
        member = repositories.ledgers.membership_for_user(ledger_id, user.id)
        if member is None or member.status != MembershipStatus.ACTIVE:
            raise PermissionDeniedError("不是该账本的在册成员")
        if owner and member.role != Role.OWNER:
            raise PermissionDeniedError("此操作仅账本负责人可执行")
        return LedgerAccess(ledger=ledger, membership=member)

    def log(
        self, repositories: Repositories, actor: Membership, object_id: UUID,
        action: str, details: dict,
    ) -> None:
        """在调用方事务中追加操作记录。"""
        payload = json.dumps(details, ensure_ascii=False, default=str, allow_nan=False)
        repositories.audit.append(AuditEvent(
            ledger_id=actor.ledger_id, actor_member_id=actor.id, object_id=object_id,
            action=action, details=payload,
            created_at=self.auth.clock(),
        ))

    def create(self, token: str, name: str, budget_cents: int | None) -> LedgerAccess:
        """负责人只能来自当前会话，客户端不能指定创建者。"""
        with self.store.transaction() as repositories:
            user = self.auth.authenticate(repositories, token)
            ledger = Ledger(name=name.strip(), budget_cents=budget_cents,
                            created_at=self.auth.clock())
            owner = Membership(user_id=user.id, ledger_id=ledger.id, role=Role.OWNER,
                               joined_at=self.auth.clock())
            repositories.ledgers.create(ledger, owner)
            self.log(repositories, owner, ledger.id, "ledger.created",
                      {"name": ledger.name, "budget_cents": ledger.budget_cents})
            return LedgerAccess(ledger=ledger, membership=owner)

    def list_for_user(self, token: str) -> tuple[LedgerAccess, ...]:
        """只列当前用户仍在册的账本。"""
        with self.store.transaction(write=False) as repositories:
            user = self.auth.authenticate(repositories, token)
            return tuple(LedgerAccess(
                ledger=ledger,
                membership=repositories.ledgers.membership_for_user(ledger.id, user.id),
            ) for ledger in repositories.ledgers.for_user(user.id))

    def get(self, token: str, ledger_id: UUID) -> LedgerAccess:
        """每次读取都校验当前成员状态。"""
        with self.store.transaction(write=False) as repositories:
            return self.scope(repositories, token, ledger_id)

    def update(self, token: str, ledger_id: UUID, changes: dict) -> LedgerAccess:
        """负责人可修改名称、预算和归档标志，不接收任意模型字段。"""
        if not changes or set(changes) - {"name", "budget_cents", "archived"}:
            raise ValidationError("只能更新账本名称、预算或归档状态")
        with self.store.transaction() as repositories:
            scope = self.scope(repositories, token, ledger_id, owner=True)
            updated = replace(scope.ledger, **changes)
            repositories.ledgers.save_settings(updated)
            if updated.budget_cents != scope.ledger.budget_cents or (
                    scope.ledger.archived and not updated.archived):
                previous_budget = None if scope.ledger.archived else scope.ledger.budget_cents
                repositories.budget_alerts.after_change(
                    repositories.reporting, updated, previous_budget, 0, self.auth.clock(),
                )
            self.log(repositories, scope.membership, ledger_id, "ledger.updated", {
                "before": {key: getattr(scope.ledger, key) for key in changes},
                "after": {key: getattr(updated, key) for key in changes},
            })
            return LedgerAccess(ledger=updated, membership=scope.membership)

    def members(
        self, token: str, ledger_id: UUID, *, include_exited: bool = False,
    ) -> tuple[MemberInfo, ...]:
        """所有在册成员均可查看共享成员列表。"""
        with self.store.transaction(write=False) as repositories:
            self.scope(repositories, token, ledger_id)
            return tuple(
                MemberInfo(membership=member, name=repositories.users.get(member.user_id).name)
                for member in repositories.ledgers.members(ledger_id, include_exited=include_exited)
            )

    def invite(self, token: str, ledger_id: UUID, username: str, role: Role) -> Invitation:
        """负责人邀请已注册账号，接受前不会直接成为成员。"""
        self._ordinary_role(role)
        username = normalize_username(username)
        with self.store.transaction() as repositories:
            scope = self.scope(repositories, token, ledger_id, owner=True)
            if scope.ledger.archived:
                raise ConflictError("归档账本不能邀请新成员")
            target = repositories.accounts.find(username)
            if target is None:
                raise NotFoundError("目标账号尚未注册")
            existing = repositories.ledgers.membership_for_user(ledger_id, target.user_id)
            if existing is not None and existing.status == MembershipStatus.ACTIVE:
                raise ConflictError("目标用户已经是该账本的在册成员")
            now = self.auth.clock()
            repositories.invitations.revoke_pending(
                ledger_id, now, target=target.user_id, expired_only=True,
            )
            invitation = Invitation(
                ledger_id=ledger_id, target_user_id=target.user_id,
                inviter_member_id=scope.membership.id, role=role,
                created_at=now, expires_at=now + INVITATION_DURATION,
            )
            repositories.invitations.add(invitation)
            self.log(repositories, scope.membership, invitation.id, "invitation.created",
                      {"target_user_id": target.user_id, "role": role.value})
            return invitation

    def invitations(self, token: str, ledger_id: UUID) -> tuple[Invitation, ...]:
        """负责人可核对账本的邀请历史。"""
        with self.store.transaction(write=False) as repositories:
            self.scope(repositories, token, ledger_id, owner=True)
            return repositories.invitations.for_ledger(ledger_id)

    def my_invitations(self, token: str) -> tuple[InvitationInfo, ...]:
        """账号只能查看发给自己的待处理邀请。"""
        with self.store.transaction(write=False) as repositories:
            user = self.auth.authenticate(repositories, token)
            result = []
            for invitation in repositories.invitations.for_user(user.id, self.auth.clock()):
                inviter = repositories.ledgers.get_member(invitation.inviter_member_id)
                result.append(InvitationInfo(
                    invitation=invitation,
                    ledger_name=repositories.ledgers.get(invitation.ledger_id).name,
                    inviter_name=repositories.users.get(inviter.user_id).name,
                ))
            return tuple(result)

    def accept(self, token: str, invitation_id: UUID) -> Membership:
        """重复接受不会重置已调整的角色，退出后不能重放旧邀请。"""
        with self.store.transaction() as repositories:
            user = self.auth.authenticate(repositories, token)
            invitation = repositories.invitations.get(invitation_id)
            if invitation.target_user_id != user.id:
                raise PermissionDeniedError("不能接受发给其他用户的邀请")
            existing = repositories.ledgers.membership_for_user(invitation.ledger_id, user.id)
            if invitation.status == "accepted":
                if existing is not None and existing.status == MembershipStatus.ACTIVE:
                    return existing
                raise ConflictError("邀请已经使用，重新加入需负责人再次邀请")
            self._check_invitation(repositories, invitation)
            if existing is not None and existing.status == MembershipStatus.ACTIVE:
                raise ConflictError("已经是账本成员")
            member = (replace(existing, role=invitation.role, status=MembershipStatus.ACTIVE)
                      if existing else Membership(
                          user_id=user.id, ledger_id=invitation.ledger_id,
                          role=invitation.role, joined_at=self.auth.clock(),
                      ))
            if existing:
                repositories.ledgers.save_member(member)
            else:
                repositories.ledgers.add_member(member)
            if member.role == Role.REVIEWER:
                ledger = repositories.ledgers.get(member.ledger_id)
                self._new_budget_recipient(repositories, ledger, member, None)
            repositories.invitations.resolve(invitation.id, "accepted", self.auth.clock())
            self.log(repositories, member, member.id, "membership.joined",
                      {"invitation_id": invitation.id, "role": member.role.value})
            return member

    def _check_invitation(self, repositories: Repositories, invitation: Invitation) -> None:
        if invitation.status != "pending" or invitation.expires_at <= self.auth.clock():
            raise ConflictError("邀请已撤销、已处理或已过期")
        if repositories.ledgers.get(invitation.ledger_id).archived:
            raise ConflictError("账本已归档，暂不能加入")
        inviter = repositories.ledgers.get_member(invitation.inviter_member_id)
        if inviter.status != MembershipStatus.ACTIVE or inviter.role != Role.OWNER:
            raise ConflictError("邀请发出者已无负责人权限，请重新获取邀请")

    def revoke_invitation(self, token: str, ledger_id: UUID, invitation_id: UUID) -> Invitation:
        """撤销未使用邀请；移除已加入成员须走成员移除接口。"""
        with self.store.transaction() as repositories:
            scope = self.scope(repositories, token, ledger_id, owner=True)
            invitation = repositories.invitations.get(invitation_id)
            if invitation.ledger_id != ledger_id:
                raise NotFoundError("邀请不属于此账本")
            if invitation.status == "accepted":
                raise ConflictError("邀请已接受，请使用成员移除操作")
            if invitation.status == "pending":
                repositories.invitations.resolve(invitation_id, "revoked", self.auth.clock())
                self.log(repositories, scope.membership, invitation.id, "invitation.revoked", {})
            return repositories.invitations.get(invitation.id)

    @staticmethod
    def _ordinary_role(role: Role) -> None:
        if not isinstance(role, Role) or role not in {Role.MEMBER, Role.REVIEWER}:
            raise ValidationError("成员身份只能设为普通成员或审核员，负责人须单独转交")

    @staticmethod
    def _target(repositories: Repositories, ledger_id: UUID, member_id: UUID) -> Membership:
        target = repositories.ledgers.get_member(member_id)
        if target.ledger_id != ledger_id:
            raise NotFoundError("成员不属于此账本")
        return target

    def _new_budget_recipient(self, repositories, ledger, member, previous_role):
        """接任者获得提醒权限时，补发当前档位，已有权限的档位不重复发。"""
        if ledger.archived or not ledger.budget_cents or member.role == Role.MEMBER:
            return
        overview = repositories.reporting.budget(ledger)
        level = overview["level"]
        allowed = {Role.OWNER, Role.REVIEWER} if level == "over" else {Role.OWNER}
        if level != "none" and member.role in allowed and previous_role not in allowed:
            repositories.budget_alerts.publish(ledger.id, overview["expense"], ledger.budget_cents,
                                              self.auth.clock(), recipient=member.id)

    @staticmethod
    def _permission_reason(reason):
        require_text(reason, "变更原因")
        if len(reason.strip()) > 500:
            raise ValidationError("变更原因最多 500 字")
        return reason.strip()

    def change_role(self, token: str, ledger_id: UUID, member_id: UUID,
                    role: Role, reason: str) -> Membership:
        """仅负责人调整在册成员角色，不能通过通用接口制造第二个负责人。"""
        self._ordinary_role(role)
        with self.store.transaction() as repositories:
            scope = self.scope(repositories, token, ledger_id, owner=True)
            target = self._target(repositories, ledger_id, member_id)
            if target.role == Role.OWNER or target.status != MembershipStatus.ACTIVE:
                raise ConflictError("不能直接修改负责人或退出成员的身份")
            reason = self._permission_reason(reason)
            updated = replace(target, role=role)
            if updated != target:
                repositories.ledgers.save_member(updated)
                repositories.budget_alerts.discard_ineligible(target.id)
                self._new_budget_recipient(repositories, scope.ledger, updated, target.role)
                self.log(repositories, scope.membership, target.id, "membership.role_changed",
                          {"before": target.role.value, "after": role.value, "reason": reason})
            return updated

    def transfer(self, token: str, ledger_id: UUID, member_id: UUID, reason: str) -> Membership:
        """转交时原负责人变为普通成员；两次角色更新及日志原子保存。"""
        with self.store.transaction() as repositories:
            scope = self.scope(repositories, token, ledger_id, owner=True)
            target = self._target(repositories, ledger_id, member_id)
            if target.id == scope.membership.id or target.status != MembershipStatus.ACTIVE:
                raise ConflictError("请指定另一位在册成员接任")
            reason = self._permission_reason(reason)
            repositories.ledgers.save_member(replace(scope.membership, role=Role.MEMBER))
            repositories.budget_alerts.discard_ineligible(scope.membership.id)
            owner = repositories.ledgers.save_member(replace(target, role=Role.OWNER))
            self._new_budget_recipient(repositories, scope.ledger, owner, target.role)
            revoked = repositories.invitations.revoke_pending(ledger_id, self.auth.clock())
            self.log(repositories, scope.membership, target.id, "ledger.owner_transferred",
                      {"previous_owner": scope.membership.id, "new_owner": owner.id,
                       "revoked_invitations": revoked, "reason": reason})
            return owner

    def leave(self, token: str, ledger_id: UUID) -> Membership:
        """主动退出前须处理待审记录，不清除历史与欠款关联。"""
        with self.store.transaction() as repositories:
            scope = self.scope(repositories, token, ledger_id)
            if scope.membership.role == Role.OWNER:
                raise ConflictError("负责人须先转交账本再退出")
            if repositories.ledgers.pending_count(ledger_id, scope.membership.id):
                raise ConflictError("请先撤回待审记录或由审核人员处理后再退出")
            return self._exit(repositories, scope.membership, scope.membership, removed=False)

    def remove(self, token: str, ledger_id: UUID, member_id: UUID) -> Membership:
        """负责人移除成员时保留待审核记录，交当前审核人员后续处理。"""
        with self.store.transaction() as repositories:
            scope = self.scope(repositories, token, ledger_id, owner=True)
            target = self._target(repositories, ledger_id, member_id)
            if target.role == Role.OWNER:
                raise ConflictError("不能移除负责人，请先转交账本")
            if target.status == MembershipStatus.EXITED:
                return target
            return self._exit(repositories, scope.membership, target, removed=True)

    def _exit(
        self, repositories: Repositories, actor: Membership, target: Membership, *, removed: bool,
    ) -> Membership:
        pending = repositories.ledgers.pending_count(target.ledger_id, target.id)
        updated = repositories.ledgers.save_member(replace(target, status=MembershipStatus.EXITED))
        repositories.invitations.revoke_pending(
            target.ledger_id, self.auth.clock(), target=target.user_id,
        )
        self.log(repositories, actor, target.id,
                  "membership.removed" if removed else "membership.left",
                  {"before": target.status.value, "after": updated.status.value,
                   "pending_records_retained": pending, "history_and_balances_retained": True})
        return updated

    def logs(
        self, token: str, ledger_id: UUID, filters: LogFilters | None = None, **criteria,
    ) -> tuple[AuditEvent, ...]:
        """在册成员均可查询账本日志，退出成员不能通过旧页面访问。"""
        with self.store.transaction(write=False) as repositories:
            self.scope(repositories, token, ledger_id)
            return repositories.audit.filtered(ledger_id, filters or LogFilters(**criteria))
