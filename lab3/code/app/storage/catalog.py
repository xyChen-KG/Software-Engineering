"""用户、账本和成员的基础读写；不代替后续服务层的登录鉴权。"""

import sqlite3
from datetime import datetime
from uuid import UUID

from app.errors import NotFoundError, ValidationError
from app.models import Ledger, Membership, MembershipStatus, Role, User
from app.storage.common import Repository, encode_time


def read_member(row: sqlite3.Row) -> Membership:
    """还原成员关系，包括已退出状态。"""
    return Membership(
        id=UUID(row["id"]), user_id=UUID(row["user_id"]), ledger_id=UUID(row["ledger_id"]),
        role=Role(row["role"]), status=MembershipStatus(row["status"]),
        joined_at=datetime.fromisoformat(row["joined_at"]),
    )


def read_ledger(row: sqlite3.Row) -> Ledger:
    """还原可选预算和归档标志。"""
    return Ledger(
        id=UUID(row["id"]), name=row["name"], budget_cents=row["budget_cents"],
        archived=bool(row["archived"]), created_at=datetime.fromisoformat(row["created_at"]),
    )


class UserRepository(Repository):
    """姓名不作唯一键，避免同名成员无法建立身份资料。"""

    def add(self, user: User) -> User:
        """保存身份资料，不保存密码明文或假定已完成注册。"""
        self._execute(
            "INSERT INTO users(id, name, created_at) VALUES (?, ?, ?)",
            (str(user.id), user.name, encode_time(user.created_at)),
        )
        return user

    def get(self, user_id: UUID) -> User:
        """按业务编号查询用户。"""
        row = self._one("SELECT * FROM users WHERE id = ?", (str(user_id),), "用户不存在")
        return User(
            id=UUID(row["id"]), name=row["name"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class LedgerRepository(Repository):
    """创建账本时同时创建负责人；成员退出后仍保留其关联。"""

    def create(self, ledger: Ledger, owner: Membership) -> Ledger:
        """账本与负责人原子保存，不留下没有负责人的新账本。"""
        if (
            owner.ledger_id != ledger.id or owner.role != Role.OWNER
            or owner.status != MembershipStatus.ACTIVE
        ):
            raise ValidationError("创建账本时必须提供该账本的在册负责人")
        with self._atomic():
            self._execute(
                "INSERT INTO ledgers(id, name, budget_cents, archived, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(ledger.id), ledger.name, ledger.budget_cents,
                 int(ledger.archived), encode_time(ledger.created_at)),
            )
            self._insert_member(owner)
        return ledger

    def add_member(self, member: Membership) -> Membership:
        """新增成员；负责人转交与退出业务将在服务层单独实现。"""
        if member.role == Role.OWNER:
            raise ValidationError("新增普通成员或审核员，负责人转交需要单独处理")
        self.get(member.ledger_id)
        self._insert_member(member)
        return member

    def _insert_member(self, member: Membership) -> None:
        self._execute(
            "INSERT INTO memberships(id, user_id, ledger_id, role, status, joined_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(member.id), str(member.user_id), str(member.ledger_id),
             member.role.value, member.status.value, encode_time(member.joined_at)),
        )

    def get(self, ledger_id: UUID) -> Ledger:
        """返回指定账本；权限判断由调用方的服务层负责。"""
        return read_ledger(self._one(
            "SELECT * FROM ledgers WHERE id = ?", (str(ledger_id),), "账本不存在"
        ))

    def get_member(self, member_id: UUID) -> Membership:
        """按成员关系编号查询，不把用户编号误当成账本内身份。"""
        return read_member(self._one(
            "SELECT * FROM memberships WHERE id = ?", (str(member_id),), "成员关系不存在"
        ))

    def members(self, ledger_id: UUID, *, include_exited: bool = False) -> tuple[Membership, ...]:
        """默认只列在册成员；内部历史查询可以包含退出成员。"""
        self.get(ledger_id)
        condition = "" if include_exited else " AND status = 'active'"
        rows = self._execute(
            "SELECT * FROM memberships WHERE ledger_id = ?" + condition + " ORDER BY joined_at, id",
            (str(ledger_id),),
        )
        return tuple(read_member(row) for row in rows)

    def for_user(self, user_id: UUID) -> tuple[Ledger, ...]:
        """只列出该用户仍在册的账本，退出关系继续留在数据库中。"""
        rows = self._execute(
            "SELECT l.* FROM ledgers l JOIN memberships m ON m.ledger_id = l.id "
            "WHERE m.user_id = ? AND m.status = 'active' ORDER BY l.created_at DESC, l.id",
            (str(user_id),),
        )
        return tuple(read_ledger(row) for row in rows)

    def membership_for_user(self, ledger_id: UUID, user_id: UUID) -> Membership | None:
        """包含退出关系，服务层据此决定拒绝访问或复用历史编号。"""
        row = self._execute(
            "SELECT * FROM memberships WHERE ledger_id=? AND user_id=?",
            (str(ledger_id), str(user_id)),
        ).fetchone()
        return read_member(row) if row is not None else None

    def save_settings(self, ledger: Ledger) -> Ledger:
        """服务层先校验负责人；只更新账本设置，不更换业务编号或时间。"""
        result = self._execute(
            "UPDATE ledgers SET name=?, budget_cents=?, archived=? WHERE id=?",
            (ledger.name, ledger.budget_cents, int(ledger.archived), str(ledger.id)),
        )
        if result.rowcount != 1:
            raise NotFoundError("账本不存在")
        return ledger

    def save_member(self, member: Membership) -> Membership:
        """只修改角色与状态，保留原用户、账本、编号和首次加入时间。"""
        result = self._execute(
            "UPDATE memberships SET role=?, status=? WHERE id=? AND ledger_id=? AND user_id=?",
            (member.role.value, member.status.value, str(member.id),
             str(member.ledger_id), str(member.user_id)),
        )
        if result.rowcount != 1:
            raise NotFoundError("成员关系不存在")
        return member

    def pending_count(self, ledger_id: UUID, member_id: UUID) -> int:
        """主动退出前检查待审核记录，不自动通过或删除。"""
        return self._execute(
            "SELECT count(*) FROM entries WHERE ledger_id=? AND submitter_member_id=? "
            "AND status='pending'", (str(ledger_id), str(member_id)),
        ).fetchone()[0]
