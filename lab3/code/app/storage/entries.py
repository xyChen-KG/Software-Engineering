"""保存收支及不可变版本，处理请求去重与乐观并发检查。"""

import sqlite3
from dataclasses import replace
from datetime import date, datetime
from uuid import UUID

from app.errors import ConflictError, ValidationError
from app.models import EntryRecord, EntrySnapshot, EntryStatus, EntryType, RecordVersion, Split
from app.models.validation import require_enum, require_integer
from app.storage.common import Repository, encode_time


def read_entry(row: sqlite3.Row) -> EntryRecord:
    """还原头信息，版本内容通过单独查询读取。"""
    return EntryRecord(
        id=UUID(row["id"]), ledger_id=UUID(row["ledger_id"]),
        submitter_member_id=UUID(row["submitter_member_id"]), kind=EntryType(row["kind"]),
        request_id=UUID(row["request_id"]), status=EntryStatus(row["status"]),
        current_version=row["current_version"], revision=row["revision"],
        original_expense_id=(
            UUID(row["original_expense_id"]) if row["original_expense_id"] else None
        ),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def content_signature(content: RecordVersion) -> tuple:
    """比较业务输入，忽略重试时由服务端重新生成的编号和时间。"""
    return (
        content.amount_cents, content.category, content.purpose, content.business_date,
        content.payer_member_id, content.no_receipt_reason, content.budget_reason,
        tuple(sorted((str(split.member_id), split.amount_cents) for split in content.splits)),
    )


class EntryRepository(Repository):
    """仓储不提供直接覆盖已通过内容或删除历史版本的操作。"""

    def get(self, entry_id: UUID) -> EntryRecord:
        """按固定业务编号读取收支头。"""
        return read_entry(self._one(
            "SELECT * FROM entries WHERE id = ?", (str(entry_id),), "收支记录不存在"
        ))

    def version(self, entry_id: UUID, number: int) -> RecordVersion:
        """读取某个历史版本及属于该版本的分摊。"""
        row = self._one(
            "SELECT * FROM entry_versions WHERE entry_id = ? AND number = ?",
            (str(entry_id), number), "记录版本不存在",
        )
        splits = tuple(
            Split(member_id=UUID(split["member_id"]), amount_cents=split["amount_cents"])
            for split in self._execute(
                "SELECT * FROM splits WHERE entry_id = ? AND version_number = ? ORDER BY member_id",
                (str(entry_id), number),
            )
        )
        return RecordVersion(
            entry_id=UUID(row["entry_id"]), ledger_id=UUID(row["ledger_id"]), number=row["number"],
            amount_cents=row["amount_cents"], category=row["category"], purpose=row["purpose"],
            business_date=date.fromisoformat(row["business_date"]),
            payer_member_id=UUID(row["payer_member_id"]), splits=splits,
            no_receipt_reason=row["no_receipt_reason"], budget_reason=row["budget_reason"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def snapshot(self, entry_id: UUID) -> EntrySnapshot:
        """在调用方事务内读取相互一致的当前头信息与内容。"""
        record = self.get(entry_id)
        return EntrySnapshot(record=record, content=self.version(entry_id, record.current_version))

    def history(self, entry_id: UUID) -> tuple[RecordVersion, ...]:
        """按版本号列出全部快照。"""
        self.get(entry_id)
        numbers = [row[0] for row in self._execute(
            "SELECT number FROM entry_versions WHERE entry_id = ? ORDER BY number", (str(entry_id),)
        )]
        return tuple(self.version(entry_id, number) for number in numbers)

    def list_headers(
        self, ledger_id: UUID, *, status: EntryStatus | None = None
    ) -> tuple[EntryRecord, ...]:
        """按账本与可选状态读取头信息，完整组合查询后续在查询模块扩展。"""
        parameters = [str(ledger_id)]
        query = "SELECT * FROM entries WHERE ledger_id = ?"
        if status is not None:
            require_enum(status, EntryStatus, "查询状态")
            query += " AND status = ?"
            parameters.append(status.value)
        rows = self._execute(query + " ORDER BY created_at DESC, id", parameters)
        return tuple(read_entry(row) for row in rows)

    def has_request(self, request_id: UUID) -> bool:
        """同请求重试优先走内容比较，避免命中自身的疑似重复规则。"""
        return self._execute(
            "SELECT 1 FROM entries WHERE request_id = ?", (str(request_id),)
        ).fetchone() is not None

    def has_similar_expense(self, content: RecordVersion) -> bool:
        """同账本、同垫付人、同日期金额类别的待审或正式支出可能重复。"""
        return self._execute(
            "SELECT 1 FROM entries e JOIN entry_versions v "
            "ON v.entry_id=e.id AND v.number=e.current_version "
            "WHERE e.ledger_id=? AND e.id!=? AND e.kind='expense' "
            "AND e.status IN ('pending', 'approved') AND v.payer_member_id=? "
            "AND v.business_date=? AND v.amount_cents=? AND v.category=? LIMIT 1",
            (str(content.ledger_id), str(content.entry_id), str(content.payer_member_id),
             content.business_date.isoformat(), content.amount_cents, content.category),
        ).fetchone() is not None

    def add(self, record: EntryRecord, content: RecordVersion) -> EntrySnapshot:
        """新记录只能从待审核版本一开始；同一请求重试返回已有记录。"""
        proposed = EntrySnapshot(record=record, content=content)
        if (record.status, record.current_version, record.revision) != (EntryStatus.PENDING, 1, 1):
            raise ValidationError("新收支必须从待审核、内容版本一及并发版本一开始")
        existing = self._execute(
            "SELECT * FROM entries WHERE request_id = ?", (str(record.request_id),)
        ).fetchone()
        if existing is not None:
            return self._replay(read_entry(existing), proposed)
        self._check_parties(proposed)
        if record.original_expense_id is not None:
            original = self.get(record.original_expense_id)
            if (
                original.ledger_id != record.ledger_id or original.kind != EntryType.EXPENSE
                or original.status != EntryStatus.APPROVED
            ):
                raise ValidationError("退款只能关联本账本中已通过的原支出")
        with self._atomic():
            self._execute(
                "INSERT INTO entries(id, ledger_id, submitter_member_id, kind, request_id, "
                "status, current_version, revision, original_expense_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(record.id), str(record.ledger_id), str(record.submitter_member_id),
                 record.kind.value, str(record.request_id), record.status.value,
                 record.current_version, record.revision,
                 str(record.original_expense_id) if record.original_expense_id else None,
                 encode_time(record.created_at)),
            )
            self._insert_version(content)
        return self.snapshot(record.id)

    def _replay(self, existing: EntryRecord, proposed: EntrySnapshot) -> EntrySnapshot:
        original = self.version(existing.id, 1)
        if (
            existing.ledger_id != proposed.record.ledger_id
            or existing.submitter_member_id != proposed.record.submitter_member_id
            or existing.kind != proposed.record.kind
            or existing.original_expense_id != proposed.record.original_expense_id
            or content_signature(original) != content_signature(proposed.content)
        ):
            raise ConflictError("同一请求编号不能用于不同的收支内容")
        return self.snapshot(existing.id)

    def append_version(
        self, content: RecordVersion, *, expected_revision: int
    ) -> EntrySnapshot:
        """只更新待审或驳回记录；权限仍需后续认证后的业务层校验。"""
        require_integer(expected_revision, "预期并发版本", minimum=1)
        record = self.get(content.entry_id)
        if record.revision != expected_revision:
            raise ConflictError("记录已被其他操作更新，请读取最新版本")
        if record.status not in {EntryStatus.PENDING, EntryStatus.REJECTED}:
            raise ConflictError("当前状态不允许直接修改内容")
        if content.number != record.current_version + 1:
            raise ValidationError("新内容版本必须在当前版本基础上加一")
        updated = replace(record, current_version=content.number, revision=record.revision + 1,
                          status=EntryStatus.PENDING)
        proposed = EntrySnapshot(record=updated, content=content)
        self._check_parties(proposed)
        with self._atomic():
            self._insert_version(content)
            cursor = self._execute(
                "UPDATE entries SET current_version=?, revision=revision+1, status='pending' "
                "WHERE id = ? AND revision = ? AND status IN ('pending', 'rejected')",
                (content.number, str(record.id), expected_revision),
            )
            if cursor.rowcount != 1:
                raise ConflictError("记录已变化，本次修改未保存")
        return self.snapshot(record.id)

    def resolve_pending(self, record: EntryRecord, status: EntryStatus) -> EntrySnapshot:
        """处理待审核记录，保留原内容版本，仅增加并发版本。"""
        if status not in {EntryStatus.APPROVED, EntryStatus.REJECTED, EntryStatus.WITHDRAWN}:
            raise ValidationError("无效的待审核处理结果")
        cursor = self._execute(
            "UPDATE entries SET status=?, revision=revision+1 "
            "WHERE id=? AND revision=? AND status='pending'",
            (status.value, str(record.id), record.revision),
        )
        if cursor.rowcount != 1:
            raise ConflictError("记录已变化，请刷新后重试")
        return self.snapshot(record.id)

    def apply_approved_change(
        self, record: EntryRecord, proposal: RecordVersion | None,
    ) -> EntrySnapshot:
        """仅变更审批调用，保留已通过账目的所有内容版本。"""
        version = record.current_version
        status = EntryStatus.VOIDED
        with self._atomic():
            if proposal is not None:
                version += 1
                updated = replace(record, current_version=version)
                EntrySnapshot(record=updated, content=proposal)
                self._insert_version(proposal)
                status = EntryStatus.APPROVED
            cursor = self._execute(
                "UPDATE entries SET status=?, revision=revision+1, current_version=? "
                "WHERE id=? AND revision=? AND status='approved'",
                (status.value, version, str(record.id), record.revision),
            )
            if cursor.rowcount != 1:
                raise ConflictError("正式记录已变化，无法应用申请")
        return self.snapshot(record.id)

    def _check_parties(self, snapshot: EntrySnapshot) -> None:
        record, content = snapshot.record, snapshot.content
        ledger = self._one("SELECT archived FROM ledgers WHERE id = ?",
                           (str(record.ledger_id),), "账本不存在")
        if ledger["archived"]:
            raise ConflictError("归档账本不能写入收支")
        member_ids = {record.submitter_member_id, content.payer_member_id}
        member_ids.update(split.member_id for split in content.splits)
        placeholders = ",".join("?" for _ in member_ids)
        count = self._execute(
            "SELECT count(*) FROM memberships WHERE ledger_id = ? AND status = 'active' "
            f"AND id IN ({placeholders})",
            (str(record.ledger_id), *(str(member_id) for member_id in member_ids)),
        ).fetchone()[0]
        if count != len(member_ids):
            raise ValidationError("提交人、垫付或收款人、承担成员必须在册且属于同一账本")

    def _insert_version(self, content: RecordVersion) -> None:
        self._execute(
            "INSERT INTO entry_versions(entry_id, ledger_id, number, amount_cents, category, "
            "purpose, business_date, payer_member_id, no_receipt_reason, created_at, "
            "budget_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(content.entry_id), str(content.ledger_id), content.number, content.amount_cents,
             content.category, content.purpose, content.business_date.isoformat(),
             str(content.payer_member_id), content.no_receipt_reason,
             encode_time(content.created_at), content.budget_reason),
        )
        for split in content.splits:
            self._execute(
                "INSERT INTO splits(entry_id, version_number, ledger_id, member_id, amount_cents) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(content.entry_id), content.number, str(content.ledger_id),
                 str(split.member_id), split.amount_cents),
            )
