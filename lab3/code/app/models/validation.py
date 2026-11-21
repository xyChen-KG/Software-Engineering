"""供领域对象复用的类型、金额及时间校验。"""

from datetime import datetime
from enum import Enum
from uuid import UUID

from app.errors import ValidationError

MAX_INTEGER = 2**63 - 1


def require_uuid(value: UUID, field: str) -> None:
    """拒绝尚未解析的字符串及其他类型的标识符。"""
    if not isinstance(value, UUID):
        raise ValidationError(f"{field} 必须是 UUID")


def require_integer(value: int, field: str, *, minimum: int = 0) -> None:
    """金额与版本号必须是整数，不能把布尔值或浮点数当作整数。"""
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= MAX_INTEGER:
        raise ValidationError(f"{field} 必须是 {minimum} 至 {MAX_INTEGER} 之间的整数")


def require_text(value: str, field: str, *, allow_empty: bool = False) -> None:
    """检查文本；避免静默修改原始用途或说明。"""
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValidationError(f"{field} 不能为空")


def require_time(value: datetime, field: str) -> None:
    """操作时间须携带时区，防止不同客户端对时间含义理解不同。"""
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValidationError(f"{field} 必须是带时区的时间")


def require_enum(value: Enum, expected: type[Enum], field: str) -> None:
    """要求使用枚举值，而不是未经校验的字符串。"""
    if not isinstance(value, expected):
        raise ValidationError(f"{field} 必须是 {expected.__name__} 枚举")
