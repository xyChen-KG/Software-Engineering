"""凭证身份、上传输入与可公开的文件摘要。"""

from dataclasses import dataclass, field
from uuid import UUID

MAX_RECEIPTS = 3
MAX_RECEIPT_BYTES = 4 * 1024 * 1024
MAX_RECEIPT_BASE64 = 4 * ((MAX_RECEIPT_BYTES + 2) // 3)


@dataclass(frozen=True, kw_only=True)
class ReceiptUpload:
    """待验证的上传文件，字节内容不写入日志。"""

    filename: str
    data_base64: str = field(repr=False)


@dataclass(frozen=True, kw_only=True)
class ReceiptSelection:
    """修改时可保留当前凭证，也可仅使用新上传的文件。"""

    uploads: tuple[ReceiptUpload, ...] = ()
    keep_existing: bool = True


@dataclass(frozen=True, kw_only=True)
class ReceiptMeta:
    """版本详情只返回文件标识及元数据。"""

    id: UUID
    filename: str
    mime: str
    size: int


@dataclass(frozen=True, kw_only=True)
class ReceiptFile:
    """校验后的新文件和下载结果。"""

    filename: str
    mime: str
    sha256: str
    data: bytes = field(repr=False)
