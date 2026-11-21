"""绑定目标账号的成员邀请，不删除使用或撤销历史。"""

from datetime import datetime
from uuid import UUID

from app.models.accounts import Invitation
from app.models.enums import Role
from app.storage.common import Repository, encode_time


def read_invitation(row) -> Invitation:
    """还原邀请对象。"""
    return Invitation(
        id=UUID(row["id"]), ledger_id=UUID(row["ledger_id"]),
        target_user_id=UUID(row["target_user_id"]),
        inviter_member_id=UUID(row["inviter_member_id"]),
        role=Role(row["role"]), status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
        expires_at=datetime.fromisoformat(row["expires_at"]),
        resolved_at=datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
    )


class InvitationRepository(Repository):
    """权限和时间检查由服务层在同一事务内完成。"""

    def add(self, invitation: Invitation) -> Invitation:
        """同账本同目标账号只能有一个未处理邀请。"""
        self._execute(
            "INSERT INTO invitations(id, ledger_id, target_user_id, inviter_member_id, role, "
            "status, created_at, expires_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
            (str(invitation.id), str(invitation.ledger_id), str(invitation.target_user_id),
             str(invitation.inviter_member_id), invitation.role.value,
             encode_time(invitation.created_at), encode_time(invitation.expires_at)),
        )
        return invitation

    def get(self, invitation_id: UUID) -> Invitation:
        """按编号读取邀请。"""
        return read_invitation(self._one(
            "SELECT * FROM invitations WHERE id=?", (str(invitation_id),), "邀请不存在"
        ))

    def for_user(self, user_id: UUID, now: datetime) -> tuple[Invitation, ...]:
        """只列目标用户自己的未过期邀请。"""
        return tuple(read_invitation(row) for row in self._execute(
            "SELECT * FROM invitations WHERE target_user_id=? AND status='pending' "
            "AND expires_at>? ORDER BY created_at DESC, id",
            (str(user_id), encode_time(now)),
        ))

    def for_ledger(self, ledger_id: UUID) -> tuple[Invitation, ...]:
        """包含历史状态，便于负责人核对。"""
        return tuple(read_invitation(row) for row in self._execute(
            "SELECT * FROM invitations WHERE ledger_id=? ORDER BY created_at DESC, id",
            (str(ledger_id),),
        ))

    def resolve(self, invitation_id: UUID, status: str, now: datetime) -> None:
        """已处理邀请不会再次更改状态。"""
        self._execute(
            "UPDATE invitations SET status=?, resolved_at=? WHERE id=? AND status='pending'",
            (status, encode_time(now), str(invitation_id)),
        )

    def revoke_pending(
        self, ledger_id: UUID, now: datetime, *, target: UUID | None = None,
        expired_only: bool = False,
    ) -> int:
        """退出、转交或重新邀请前撤销旧邀请，防止旧页面恢复已失去的权限。"""
        condition = "ledger_id=? AND status='pending'"
        parameters = [encode_time(now), str(ledger_id)]
        if target is not None:
            condition += " AND target_user_id=?"
            parameters.append(str(target))
        if expired_only:
            condition += " AND expires_at<=?"
            parameters.append(encode_time(now))
        return self._execute(
            "UPDATE invitations SET status='revoked', resolved_at=? WHERE " + condition,
            parameters,
        ).rowcount
