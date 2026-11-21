"""已审核收支的更正申请、作废申请与复核。"""

from dataclasses import dataclass, replace
from uuid import UUID, uuid4

from app.errors import ConflictError, PermissionDeniedError, ValidationError
from app.models import EntryRecord, EntrySnapshot, EntryStatus, EntryType, Role
from app.models.changes import ChangeKind, ChangeQueueRow, ChangeRequest, ChangeStatus
from app.models.entry_workflow import EntryDraft
from app.models.receipts import ReceiptSelection
from app.services.entries import REVIEW_ROLES, EntryService
from app.services.ledgers import LedgerAccess
from app.services.receipts import prepare_receipts
from app.storage.store import Repositories


@dataclass(frozen=True, kw_only=True)
class ChangeSubmission:
    """独立于已生效版本的申请输入。"""

    kind: ChangeKind
    revision: int
    reason: str
    draft: EntryDraft | None = None
    receipts: ReceiptSelection = ReceiptSelection()


@dataclass(frozen=True, kw_only=True)
class ChangeDecision:
    """审查人明确选择申请及其结果。"""

    change_id: UUID
    status: ChangeStatus
    reason: str = ""


class ChangeService:
    """只在非申请人审核通过后修改正式状态，旧内容始终可查。"""

    def __init__(self, entries: EntryService) -> None:
        self.entries = entries

    def submit(
        self, token: str, ledger_id: UUID, entry_id: UUID, request: ChangeSubmission,
    ) -> ChangeRequest:
        """核对申请资格、基准版本、未决申请和拟改内容。"""
        entries = self.entries
        with entries.store.transaction() as repositories:
            scope = entries.ledgers.scope(repositories, token, ledger_id)
            entries.ensure_open(scope)
            record = entries.record_in_ledger(repositories, ledger_id, entry_id)
            actor = scope.membership
            if actor.id != record.submitter_member_id and actor.role != Role.OWNER:
                raise PermissionDeniedError("只有原提交人或负责人可以申请")
            if record.status != EntryStatus.APPROVED or record.revision != request.revision:
                reason = "仅可基于当前已通过记录发起变更，请刷新后再试"
            elif repositories.changes.pending(entry_id):
                reason = "这笔账已有待审核变更"
            else:
                return self._submit(repositories, scope, record, request)
            entries.ledgers.log(repositories, actor, entry_id, "change.conflict", {
                "operation": "submit", "kind": request.kind.value,
                "expected_revision": request.revision, "current_revision": record.revision,
                "reason": reason,
            })
        raise ConflictError(reason)

    def _submit(
        self, repositories: Repositories, scope: LedgerAccess, record: EntryRecord,
        request: ChangeSubmission,
    ) -> ChangeRequest:
        reason = request.reason.strip()
        if not reason or (request.kind == ChangeKind.CORRECT) != (request.draft is not None):
            raise ValidationError("请填写申请原因，更正还须填写新内容")
        now = self.entries.clock()
        proposal = None
        receipt_ids = ()
        if request.draft is not None:
            proposal = request.draft.version(record, record.current_version + 1, now)
            self._validate_proposal(repositories, record, proposal)
            self.entries.check_budget(repositories, scope, record, proposal)
            self.entries.check_duplicate(repositories, record, proposal, request.draft)
            receipt_ids = prepare_receipts(
                repositories.receipts, record, proposal, request.receipts,
            )
        change = ChangeRequest(
            id=uuid4(), ledger_id=record.ledger_id, entry_id=record.id,
            base_version=record.current_version, base_revision=record.revision,
            kind=request.kind, applicant_member_id=scope.membership.id, reason=reason,
            proposal=proposal, created_at=now,
        )
        repositories.changes.add(change)
        repositories.receipts.link_change(record, change.id, receipt_ids)
        self.entries.ledgers.log(repositories, scope.membership, record.id, "change.submitted", {
            "change_id": change.id, "kind": change.kind.value, "base_version": change.base_version,
            "reason": reason, "receipt_ids": receipt_ids,
            "confirmed_duplicate_reason": request.draft.duplicate_reason if request.draft else "",
        })
        return change

    def decide(
        self, token: str, ledger_id: UUID, entry_id: UUID, decision: ChangeDecision,
    ) -> ChangeRequest:
        """审批、正式版本、凭证和日志一起提交。"""
        entries = self.entries
        with entries.store.transaction() as repositories:
            scope = entries.ledgers.scope(repositories, token, ledger_id)
            entries.ensure_open(scope)
            record = entries.record_in_ledger(repositories, ledger_id, entry_id)
            change = repositories.changes.get(entry_id, decision.change_id)
            actor = scope.membership
            if actor.role not in REVIEW_ROLES or actor.id == change.applicant_member_id:
                raise PermissionDeniedError("仅非申请人的负责人或审核员可审核")
            if change.status != ChangeStatus.PENDING:
                reason = "申请已处理，请刷新后查看结果"
                entries.ledgers.log(repositories, actor, entry_id, "change.conflict", {
                    "operation": "review", "change_id": change.id,
                    "status": change.status.value, "reason": reason,
                })
            else:
                if decision.status not in {ChangeStatus.APPROVED, ChangeStatus.REJECTED}:
                    raise ValidationError("不支持的审核结果")
                if decision.status == ChangeStatus.REJECTED and not decision.reason.strip():
                    raise ValidationError("驳回申请须填写原因")
                result = replace(change, status=decision.status, reviewer_member_id=actor.id,
                                 review_reason=decision.reason.strip(), reviewed_at=entries.clock())
                self._apply_decision(repositories, scope, record, result)
                return result
        raise ConflictError(reason)

    def _apply_decision(
        self, repositories: Repositories, scope: LedgerAccess, record: EntryRecord,
        result: ChangeRequest,
    ) -> None:
        before = repositories.entries.version(record.id, record.current_version)
        final = EntrySnapshot(record=record, content=before)
        if result.status == ChangeStatus.APPROVED:
            if (record.status != EntryStatus.APPROVED or record.revision != result.base_revision
                    or record.current_version != result.base_version):
                raise ConflictError("正式记录已变化，请查看详情后重新申请")
            proposal = result.proposal
            if proposal is not None:
                self._validate_proposal(repositories, record, proposal)
                proposal = replace(proposal, created_at=result.reviewed_at)
            final = repositories.entries.apply_approved_change(record, proposal)
            if record.kind == EntryType.EXPENSE:
                delta = (final.content.amount_cents if proposal else 0) - before.amount_cents
                if delta > 0:
                    repositories.budget_alerts.after_change(
                        repositories.reporting, scope.ledger, scope.ledger.budget_cents,
                        delta, result.reviewed_at,
                    )
            if proposal is not None:
                ids = repositories.receipts.change_ids(result.id)
                repositories.receipts.link_version(record, final.content.number, ids)
        repositories.changes.resolve(result)
        self.entries.ledgers.log(
            repositories, scope.membership, record.id, "change." + result.status.value,
            {
                "change_id": result.id, "kind": result.kind.value,
                "reason": result.reason, "review_reason": result.review_reason,
                "before": {"amount_cents": before.amount_cents, "status": record.status.value,
                           "version": before.number},
                "after": {"amount_cents": final.content.amount_cents,
                          "status": final.record.status.value, "version": final.content.number},
            },
        )

    def queue(
        self, token: str, ledger_id: UUID, limit: int, offset: int,
    ) -> tuple[ChangeQueueRow, ...]:
        """审核中心仅向当前具有审核角色的在册成员开放。"""
        with self.entries.store.transaction(write=False) as repositories:
            scope = self.entries.ledgers.scope(repositories, token, ledger_id)
            if scope.membership.role not in REVIEW_ROLES:
                raise PermissionDeniedError("当前身份没有审核权限")
            return repositories.changes.queue(ledger_id, limit, offset)

    @staticmethod
    def _validate_proposal(repositories: Repositories, record: EntryRecord, proposal) -> None:
        EntrySnapshot(record=replace(record, current_version=record.current_version + 1),
                      content=proposal)
        original = repositories.entries.version(record.id, record.current_version)
        old_parties = {original.payer_member_id, *(item.member_id for item in original.splits)}
        new_parties = {proposal.payer_member_id, *(item.member_id for item in proposal.splits)}
        active = {member.id for member in repositories.ledgers.members(record.ledger_id)}
        if new_parties - old_parties - active:
            raise ValidationError("新增垫付、收款或分摊成员必须是本账本的在册成员")
