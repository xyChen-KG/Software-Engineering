"""校验凭证上传和版本继承规则。"""

import base64
import hashlib
from io import BytesIO

from PIL import Image

from app.errors import ConflictError, ValidationError
from app.models import EntryRecord, EntryType, RecordVersion
from app.models.receipts import (
    MAX_RECEIPT_BASE64, MAX_RECEIPT_BYTES, MAX_RECEIPTS,
    ReceiptFile, ReceiptSelection, ReceiptUpload,
)
from app.storage.receipts import ReceiptRepository

MAX_PIXELS = 16_000_000
FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png"}


def validate_upload(upload: ReceiptUpload) -> ReceiptFile:
    """检查文件字节、可解码格式和像素规模，保留原文件。"""
    filename = upload.filename.strip()
    if not filename or filename in {".", ".."} or len(filename) > 120 or any(
        character in "/\\:" or not character.isprintable() for character in filename
    ):
        raise ValidationError("凭证文件名不合法")
    encoded = upload.data_base64
    if len(encoded) > MAX_RECEIPT_BASE64:
        raise ValidationError("每张凭证最多 4 MiB")
    try:
        data = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise ValidationError("凭证编码无效") from exc
    if not 1 <= len(data) <= MAX_RECEIPT_BYTES:
        raise ValidationError("凭证不能为空且每张最多 4 MiB")
    try:
        with Image.open(BytesIO(data), formats=tuple(FORMATS)) as photo:
            if photo.width * photo.height > MAX_PIXELS or getattr(photo, "n_frames", 1) != 1:
                raise ValidationError("凭证必须是单帧图片且最多 1600 万像素")
            mime = FORMATS[photo.format]
            photo.load()
    except (OSError, Image.DecompressionBombError, SyntaxError) as exc:
        raise ValidationError("凭证须为可读取的 JPG 或 PNG 图片") from exc
    return ReceiptFile(filename=filename, mime=mime, sha256=hashlib.sha256(data).hexdigest(),
                       data=data)


def validated_files(selection: ReceiptSelection) -> tuple[ReceiptFile, ...]:
    """次数及大小限制也适用于直接调用领域服务。"""
    if len(selection.uploads) > MAX_RECEIPTS:
        raise ValidationError("一份内容最多保存 3 张凭证")
    return tuple(validate_upload(file) for file in selection.uploads)


def verify_replay(
    repository: ReceiptRepository, record: EntryRecord, files: tuple[ReceiptFile, ...],
) -> None:
    """请求编号重试须与首次上传内容一致。"""
    signatures = {(file.filename, file.sha256) for file in files}
    if repository.signatures(record.id, 1) != signatures:
        raise ConflictError("同一请求编号不能使用不同的凭证")


def prepare_receipts(
    repository: ReceiptRepository, record: EntryRecord, content: RecordVersion,
    selection: ReceiptSelection, *, initial: bool = False,
):
    """选择下一版凭证。失败时由本次业务事务回滚文件写入。"""
    files = validated_files(selection)
    retained = ()
    if selection.keep_existing and not initial:
        retained = repository.version_ids(record.id, record.current_version)
    ids = tuple(dict.fromkeys((*retained, *(repository.put(record, file) for file in files))))
    if len(ids) > MAX_RECEIPTS:
        raise ValidationError("保留和新上传的凭证合计最多 3 张")
    if record.kind == EntryType.EXPENSE and not ids and not content.no_receipt_reason:
        raise ValidationError("未上传凭证的支出须填写无凭证说明")
    return ids
