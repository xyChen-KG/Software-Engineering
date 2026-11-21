"""凭证内容与版本关联在 SQLite 同事务保存。"""

from uuid import UUID, uuid4

from app.models import EntryRecord
from app.models.receipts import ReceiptFile, ReceiptMeta
from app.storage.common import Repository


class ReceiptRepository(Repository):
    """同一记录重复使用相同凭证时复用原文件。"""

    def put(self, record: EntryRecord, receipt: ReceiptFile) -> UUID:
        """在当前收支范围内保存或复用凭证字节。"""
        self._execute(
            "INSERT INTO receipt_files(id, entry_id, ledger_id, filename, mime, sha256, body) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(entry_id, sha256, filename) DO NOTHING",
            (str(uuid4()), str(record.id), str(record.ledger_id), receipt.filename, receipt.mime,
             receipt.sha256, receipt.data),
        )
        return UUID(self._one(
            "SELECT id FROM receipt_files WHERE entry_id=? AND sha256=? AND filename=?",
            (str(record.id), receipt.sha256, receipt.filename), "凭证保存失败",
        )["id"])

    def version_ids(self, entry_id: UUID, version: int) -> tuple[UUID, ...]:
        """读取版本已有凭证，用于继承或去重。"""
        rows = self._execute(
            "SELECT receipt_id FROM version_receipts WHERE entry_id=? AND version_number=?",
            (str(entry_id), version),
        )
        return tuple(UUID(row["receipt_id"]) for row in rows)

    def change_ids(self, change_id: UUID) -> tuple[UUID, ...]:
        """读取申请所提议使用的凭证。"""
        rows = self._execute("SELECT receipt_id FROM change_receipts WHERE change_id=?",
                             (str(change_id),))
        return tuple(UUID(row["receipt_id"]) for row in rows)

    def link_version(self, record: EntryRecord, version: int, ids: tuple[UUID, ...]) -> None:
        """一次性把文件关联到刚创建的正式内容快照。"""
        for receipt_id in ids:
            self._execute(
                "INSERT INTO version_receipts(entry_id, version_number, ledger_id, receipt_id) "
                "VALUES (?, ?, ?, ?)",
                (str(record.id), version, str(record.ledger_id), str(receipt_id)),
            )

    def link_change(self, record: EntryRecord, change_id: UUID, ids: tuple[UUID, ...]) -> None:
        """把更正草案的图片保存在其申请下。"""
        for receipt_id in ids:
            self._execute(
                "INSERT INTO change_receipts(change_id, entry_id, ledger_id, receipt_id) "
                "VALUES (?, ?, ?, ?)",
                (str(change_id), str(record.id), str(record.ledger_id), str(receipt_id)),
            )

    def version_metadata(self, record: EntryRecord) -> dict[int, tuple[ReceiptMeta, ...]]:
        """详情展示各版本的文件信息，无需读取 BLOB。"""
        by_version = {}
        for row in self._execute(
            "SELECT v.version_number, f.id, f.filename, f.mime, length(f.body) AS size "
            "FROM version_receipts v JOIN receipt_files f ON f.id=v.receipt_id "
            "WHERE v.entry_id=? ORDER BY f.filename, f.id", (str(record.id),),
        ):
            by_version.setdefault(row["version_number"], []).append(self._meta(row))
        return {index: tuple(files) for index, files in by_version.items()}

    def change_metadata(self, change_id: UUID) -> tuple[ReceiptMeta, ...]:
        """返回申请下的凭证信息。"""
        return tuple(self._meta(row) for row in self._execute(
            "SELECT f.id, f.filename, f.mime, length(f.body) AS size "
            "FROM change_receipts c JOIN receipt_files f ON f.id=c.receipt_id "
            "WHERE c.change_id=? ORDER BY f.filename, f.id", (str(change_id),),
        ))

    def get(self, record: EntryRecord, receipt_id: UUID) -> ReceiptFile:
        """同时按记录与账本范围查询，编号无法跨记录复用。"""
        row = self._one(
            "SELECT filename, mime, sha256, body FROM receipt_files "
            "WHERE id=? AND entry_id=? AND ledger_id=?",
            (str(receipt_id), str(record.id), str(record.ledger_id)), "该记录中没有此凭证",
        )
        return ReceiptFile(filename=row["filename"], mime=row["mime"],
                           sha256=row["sha256"], data=row["body"])

    def signatures(self, entry_id: UUID, version: int) -> set[tuple[str, str]]:
        """仅在首次提交重试时比较原始凭证指纹。"""
        return {
            (row["filename"], row["sha256"]) for row in self._execute(
                "SELECT f.filename, f.sha256 FROM version_receipts v "
                "JOIN receipt_files f ON f.id=v.receipt_id "
                "WHERE v.entry_id=? AND v.version_number=?",
                (str(entry_id), version),
            )
        }

    @staticmethod
    def _meta(row) -> ReceiptMeta:
        return ReceiptMeta(id=UUID(row["id"]), filename=row["filename"], mime=row["mime"],
                           size=row["size"])
