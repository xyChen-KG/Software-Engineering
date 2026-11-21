"""账号注册、密码校验及可撤销的服务端会话。"""

import hashlib
import math
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pwdlib import PasswordHash
from pwdlib.exceptions import HashVerificationError, UnknownHashError

from app.errors import AuthenticationError, ConflictError, RateLimitError, ValidationError
from app.models import User
from app.models.accounts import Account, Session
from app.models.entities import utc_now
from app.storage.store import LedgerStore, Repositories

PASSWORD_HASHER = PasswordHash.recommended()
DUMMY_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))
SESSION_DURATION = timedelta(hours=8)
LOGIN_LOCK_DURATION = timedelta(seconds=60)
MAX_LOGIN_FAILURES = 5


def normalize_username(username: str) -> str:
    """账号名忽略首尾空格和大小写，显示姓名不受此限制。"""
    value = username.strip().lower()
    if re.fullmatch(r"[a-z0-9_]{3,32}", value) is None:
        raise ValidationError("用户名需为 3 至 32 位英文字母、数字或下划线")
    return value


def token_digest(token: str) -> str:
    """只接受服务器使用的随机令牌格式，数据库中只保存摘要。"""
    if re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
        raise AuthenticationError("登录会话无效，请重新登录")
    return hashlib.sha256(token.encode("ascii")).hexdigest()


@dataclass(frozen=True, kw_only=True)
class Profile:
    """可以返回客户端的账号资料，不包含散列与登录失败信息。"""

    user: User
    username: str


@dataclass(frozen=True, kw_only=True)
class LoginResult:
    """登录时只向调用者返回一次明文令牌。"""

    access_token: str = field(repr=False)
    expires_at: datetime
    profile: Profile


class AuthService:
    """每次授权都查询会话，不信任客户端提供的用户编号或角色。"""

    def __init__(
        self, store: LedgerStore, *, clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.store = store
        self.clock = clock

    def register(self, username: str, password: str, name: str) -> Profile:
        """账号及用户原子创建，密码使用 Argon2 散列。"""
        username = normalize_username(username)
        if not 15 <= len(password) <= 128 or not password.strip():
            raise ValidationError("密码长度应为 15 至 128 个字符，不能全为空白")
        user = User(name=name.strip(), created_at=self.clock())
        account = Account(
            user_id=user.id, username=username, password_hash=PASSWORD_HASHER.hash(password),
            created_at=self.clock(),
        )
        try:
            with self.store.transaction() as repositories:
                repositories.users.add(user)
                repositories.accounts.add(account)
        except ConflictError:
            raise ConflictError("用户名已被使用") from None
        return Profile(user=user, username=username)

    def login(self, username: str, password: str) -> LoginResult:
        """密码计算放在写锁外，失败计数和会话保存则在写事务内重查。"""
        try:
            username = normalize_username(username)
        except ValidationError:
            raise AuthenticationError("用户名或密码错误") from None
        with self.store.transaction(write=False) as repositories:
            account = repositories.accounts.find(username)
        if account is not None:
            self._check_lock(account)
        try:
            valid, updated_hash = PASSWORD_HASHER.verify_and_update(
                password, account.password_hash if account else DUMMY_HASH
            )
        except (HashVerificationError, UnknownHashError):
            valid, updated_hash = False, None
        if account is None:
            raise AuthenticationError("用户名或密码错误")
        return self._finish_login(account, valid, updated_hash)

    def _finish_login(
        self, account: Account, valid: bool, updated_hash: str | None,
    ) -> LoginResult:
        failure = None
        result = None
        with self.store.transaction() as repositories:
            current = repositories.accounts.get(account.user_id)
            self._check_lock(current)
            if current.password_hash != account.password_hash:
                raise AuthenticationError("账号状态已变化，请重新登录")
            now = self.clock()
            if not valid:
                attempts = (0 if current.locked_until else current.failed_attempts) + 1
                locked_until = now + LOGIN_LOCK_DURATION if attempts >= MAX_LOGIN_FAILURES else None
                repositories.accounts.update_login(
                    current, failed_attempts=attempts, locked_until=locked_until,
                )
                failure = (RateLimitError(60) if locked_until else
                           AuthenticationError("用户名或密码错误"))
            else:
                repositories.accounts.update_login(
                    current, failed_attempts=0, locked_until=None, password_hash=updated_hash,
                )
                token = secrets.token_urlsafe(32)
                session = Session(token_hash=token_digest(token), user_id=account.user_id,
                                  issued_at=now, expires_at=now + SESSION_DURATION)
                repositories.sessions.add(session)
                result = LoginResult(
                    access_token=token, expires_at=session.expires_at,
                    profile=Profile(user=repositories.users.get(account.user_id),
                                    username=account.username),
                )
        # 失败次数必须先提交，再向接口层抛错，不能随异常回滚。
        if failure is not None:
            raise failure
        return result

    def _check_lock(self, account: Account) -> None:
        if account.locked_until is not None and account.locked_until > self.clock():
            seconds = math.ceil((account.locked_until - self.clock()).total_seconds())
            raise RateLimitError(max(1, seconds))

    def authenticate(self, repositories: Repositories, token: str) -> User:
        """在当前工作单元内检查失效、撤销和有效期，供业务服务复用。"""
        session = repositories.sessions.find(token_digest(token))
        if session is None or session.revoked_at is not None or session.expires_at <= self.clock():
            raise AuthenticationError("登录会话无效或已过期，请重新登录")
        return repositories.users.get(session.user_id)

    def profile(self, token: str) -> Profile:
        """读取当前账号，不把凭据或会话表直接序列化。"""
        with self.store.transaction(write=False) as repositories:
            user = self.authenticate(repositories, token)
            return Profile(user=user, username=repositories.accounts.get(user.id).username)

    def logout(self, token: str) -> None:
        """校验与撤销在同一写事务内，随后所有旧请求都需重新认证。"""
        with self.store.transaction() as repositories:
            self.authenticate(repositories, token)
            repositories.sessions.revoke(token_digest(token), self.clock())
