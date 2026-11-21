"""收支登记、审核、历史和正式汇总的 HTTP 接口。"""

from datetime import date
from base64 import b64encode
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field, StrictBool, StringConstraints

from app.api.auth_routes import SessionToken
from app.api.schemas import LedgerResponse, RequestModel
from app.models import EntrySnapshot, EntryStatus, EntryType, Split
from app.models.entry_workflow import EntryDetail, EntryDraft, EntryPage
from app.models.receipts import MAX_RECEIPT_BASE64, MAX_RECEIPTS, ReceiptSelection, ReceiptUpload
from app.models.validation import MAX_INTEGER
from app.services.entries import EntryChange, EntryQuery, EntryService, Submission

Amount = Annotated[int, Field(strict=True, gt=0, le=MAX_INTEGER)]
SplitAmount = Annotated[int, Field(strict=True, ge=0, le=MAX_INTEGER)]
Revision = Annotated[int, Field(strict=True, ge=1, le=MAX_INTEGER)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
RequiredReason = Annotated[str, StringConstraints(
    strip_whitespace=True, min_length=1, max_length=500,
)]
Category = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Purpose = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class SplitInput(RequestModel):
    """单个参与成员的金额分配。"""

    member_id: UUID
    amount_cents: SplitAmount


class ReceiptInput(RequestModel):
    """上传图片，请求不包含客户端文件路径或声明的 MIME 类型。"""

    filename: Annotated[str, Field(min_length=1, max_length=120)]
    data_base64: Annotated[str, Field(min_length=1, max_length=MAX_RECEIPT_BASE64)]


class EntryInput(RequestModel):
    """业务字段，不接收操作者、账本或任意状态字段。"""

    amount_cents: Amount
    category: Category
    purpose: Purpose
    business_date: date
    payer_member_id: UUID
    splits: tuple[SplitInput, ...] = Field(default=(), max_length=100)
    no_receipt_reason: Reason = ""
    budget_reason: Reason = ""
    duplicate_reason: Reason = ""
    attachments: tuple[ReceiptInput, ...] = Field(default=(), max_length=MAX_RECEIPTS)
    keep_receipts: StrictBool = True

    def to_draft(self) -> EntryDraft:
        """转换成领域对象，保留整数分的精度。"""
        return EntryDraft(
            amount_cents=self.amount_cents, category=self.category, purpose=self.purpose,
            business_date=self.business_date, payer_member_id=self.payer_member_id,
            splits=tuple(Split(**part.model_dump()) for part in self.splits),
            no_receipt_reason=self.no_receipt_reason, duplicate_reason=self.duplicate_reason,
            budget_reason=self.budget_reason,
        )

    def receipt_selection(self) -> ReceiptSelection:
        """当前版本的图片可随新版本继承。"""
        return ReceiptSelection(
            uploads=tuple(ReceiptUpload(**item.model_dump()) for item in self.attachments),
            keep_existing=self.keep_receipts,
        )


class CreateEntry(EntryInput):
    """一次登记的业务类型和重试编号。"""

    kind: EntryType
    request_id: UUID
    original_expense_id: UUID | None = None


class EditEntry(EntryInput):
    """基于客户端看到的并发版本保存内容。"""

    revision: Revision


class ReviewInput(RequestModel):
    """审核通过或驳回。驳回理由由业务层检查。"""

    revision: Revision
    result: Literal["approved", "rejected"]
    reason: Reason = ""


class WithdrawInput(RequestModel):
    """撤回待审核记录必须说明原因。"""

    revision: Revision
    reason: RequiredReason


class EntriesResponse(BaseModel):
    """含当前权限、分页列表及整本账的正式汇总。"""

    ledger: LedgerResponse
    entries: EntryPage


class EntryFilters(BaseModel):
    """请求默认加载最近 30 笔，列表可继续翻页。"""

    status: EntryStatus | None = None
    limit: int = Field(default=30, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=MAX_INTEGER)
    review: bool = False


class EntryDetailResponse(BaseModel):
    """含当前权限和收支详情。"""

    ledger: LedgerResponse
    detail: EntryDetail


def entry_router(service: EntryService) -> APIRouter:
    """将 HTTP 参数转换成服务输入。"""
    router = APIRouter(prefix="/ledgers/{ledger_id}", tags=["收支与审核"])

    @router.get("/entries", response_model=EntriesResponse)
    def entries(ledger_id: UUID, token: SessionToken, filters: Annotated[EntryFilters, Query()]):
        scope, page = service.page(token, ledger_id, EntryQuery(**filters.model_dump()))
        return EntriesResponse(ledger=LedgerResponse.from_access(scope), entries=page)

    @router.post("/entries", response_model=EntrySnapshot, status_code=201)
    def create(ledger_id: UUID, token: SessionToken, body: CreateEntry):
        return service.submit(token, ledger_id, Submission(
            request_id=body.request_id, kind=body.kind, draft=body.to_draft(),
            original_expense_id=body.original_expense_id, receipts=body.receipt_selection(),
        ))

    @router.get("/entries/{entry_id}", response_model=EntryDetailResponse)
    def detail(ledger_id: UUID, entry_id: UUID, token: SessionToken):
        scope, item = service.detail(token, ledger_id, entry_id)
        return EntryDetailResponse(ledger=LedgerResponse.from_access(scope), detail=item)

    @router.patch("/entries/{entry_id}", response_model=EntrySnapshot)
    def edit(ledger_id: UUID, entry_id: UUID, token: SessionToken, body: EditEntry):
        return service.change(token, ledger_id, entry_id, EntryChange(
            revision=body.revision, operation="edit", draft=body.to_draft(),
            receipts=body.receipt_selection(),
        ))

    @router.post("/entries/{entry_id}/review", response_model=EntrySnapshot)
    def review_entry(ledger_id: UUID, entry_id: UUID, token: SessionToken, body: ReviewInput):
        return service.change(token, ledger_id, entry_id, EntryChange(
            revision=body.revision, operation=body.result, reason=body.reason,
        ))

    @router.post("/entries/{entry_id}/withdraw", response_model=EntrySnapshot)
    def withdraw(ledger_id: UUID, entry_id: UUID, token: SessionToken, body: WithdrawInput):
        return service.change(token, ledger_id, entry_id, EntryChange(
            revision=body.revision, operation="withdrawn", reason=body.reason,
        ))

    @router.get("/entries/{entry_id}/receipts/{receipt_id}")
    def receipt(ledger_id: UUID, entry_id: UUID, receipt_id: UUID, token: SessionToken):
        file = service.get_receipt(token, ledger_id, entry_id, receipt_id)
        return {"filename": file.filename, "mime": file.mime,
                "data_base64": b64encode(file.data).decode("ascii")}

    return router
