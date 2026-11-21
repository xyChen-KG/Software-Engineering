"""认证凭据与持久会话的内部数据访问。"""

from datetime import datetime
from uuid import UUID

from app.models.accounts import Account, Session
from app.storage.common import Repository, encode_time


class AccountRepository(Repository):
    """只在认证服务内部使用，不返回给 HTTP 客户端。"""

    def add(self, account: Account) -> None:
        """唯一用户名由数据库保证，用户与账号由上层共享事务提交。"""
        self._execute(
            "INSERT INTO accounts(user_id, username, password_hash, created_at) "
            "VALUES (?, ?, ?, ?)",
            (str(account.user_id), account.username, account.password_hash,
             encode_time(account.created_at)),
        )

    def find(self, username: str) -> Account | None:
        """按规范化的用户名查询。"""
        row = self._execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone()
        return self._read(row) if row is not None else None

    def get(self, user_id: UUID) -> Account:
        """读取用户账号，不存在时拒绝假定该用户已经注册。"""
        return self._read(self._one(
            "SELECT * FROM accounts WHERE user_id=?", (str(user_id),), "账号不存在"
        ))

    def update_login(
        self, account: Account, *, failed_attempts: int, locked_until: datetime | None,
        password_hash: str | None = None,
    ) -> None:
        """调用方在写事务内重读账号后更新，避免丢失失败次数。"""
        self._execute(
            "UPDATE accounts SET failed_attempts=?, locked_until=?, password_hash=? "
            "WHERE user_id=?",
            (failed_attempts, encode_time(locked_until) if locked_until else None,
             password_hash or account.password_hash, str(account.user_id)),
        )

    @staticmethod
    def _read(row) -> Account:
        return Account(
            user_id=UUID(row["user_id"]), username=row["username"],
            password_hash=row["password_hash"], failed_attempts=row["failed_attempts"],
            locked_until=(datetime.fromisoformat(row["locked_until"])
                          if row["locked_until"] else None),
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class SessionRepository(Repository):
    """会话只保存摘要、用户及有效期，可以立即撤销。"""

    def add(self, session: Session) -> None:
        """保存一次全新登录产生的会话。"""
        self._execute(
            "INSERT INTO sessions(token_hash, user_id, issued_at, expires_at) VALUES (?, ?, ?, ?)",
            (session.token_hash, str(session.user_id), encode_time(session.issued_at),
             encode_time(session.expires_at)),
        )

    def find(self, token_hash: str) -> Session | None:
        """解析由服务器生成的摘要，不把明文令牌写入 SQL。"""
        row = self._execute("SELECT * FROM sessions WHERE token_hash=?", (token_hash,)).fetchone()
        if row is None:
            return None
        return Session(
            token_hash=row["token_hash"], user_id=UUID(row["user_id"]),
            issued_at=datetime.fromisoformat(row["issued_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            revoked_at=datetime.fromisoformat(row["revoked_at"]) if row["revoked_at"] else None,
        )

    def revoke(self, token_hash: str, now: datetime) -> None:
        """退出登录只撤销当前会话，不影响其他设备。"""
        self._execute(
            "UPDATE sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
            (encode_time(now), token_hash),
        )
