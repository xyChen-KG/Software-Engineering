"""领域与存储层的可预期错误，后续由接口层映射为响应。"""


class ValidationError(ValueError):
    """数据不满足领域约束。"""


class NotFoundError(LookupError):
    """指定的业务对象不存在。"""


class ConflictError(RuntimeError):
    """唯一键、版本或关联状态发生冲突。"""


class DuplicateEntryError(ConflictError):
    """另一请求已登记相似账目，需要提交人说明。"""


class SchemaVersionError(RuntimeError):
    """数据库版本不受支持或数据库结构不完整。"""


class AuthenticationError(RuntimeError):
    """登录信息、会话或有效期不满足认证要求。"""


class PermissionDeniedError(RuntimeError):
    """已登录身份没有访问或操作目标对象的权限。"""


class RateLimitError(RuntimeError):
    """连续登录失败后的短时限制。"""

    def __init__(self, retry_after: int) -> None:
        super().__init__("登录尝试过多，请稍后再试")
        self.retry_after = retry_after
