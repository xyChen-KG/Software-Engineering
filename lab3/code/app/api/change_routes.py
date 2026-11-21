"""变更申请、审核与待处理队列接口。"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.api.auth_routes import SessionToken
from app.api.entry_routes import EntryInput, Reason, RequiredReason, Revision
from app.api.schemas import RequestModel
from app.models.changes import ChangeKind, ChangeQueueRow, ChangeRequest, ChangeStatus
from app.models.receipts import ReceiptSelection
from app.models.validation import MAX_INTEGER
from app.services.changes import ChangeDecision, ChangeService, ChangeSubmission


class CorrectInput(EntryInput):
    """更正草案与基准并发版本。"""

    kind: Literal["correct"]
    revision: Revision
    reason: RequiredReason


class VoidInput(RequestModel):
    """作废只提交理由和基准版本。"""

    kind: Literal["void"]
    revision: Revision
    reason: RequiredReason


class DecideInput(RequestModel):
    """决定适用于指定的一次申请。"""

    result: Literal["approved", "rejected"]
    reason: Reason = ""


class ChangeQueue(BaseModel):
    """分批显示待审申请。"""

    items: tuple[ChangeQueueRow, ...]
    next_offset: int | None


def change_router(service: ChangeService) -> APIRouter:
    """所有接口在服务层核对登录和相关账本。"""
    router = APIRouter(prefix="/ledgers/{ledger_id}", tags=["更正与作废"])

    @router.post("/entries/{entry_id}/changes", response_model=ChangeRequest, status_code=201)
    def submit(
        ledger_id: UUID, entry_id: UUID, token: SessionToken,
        body: Annotated[CorrectInput | VoidInput, Field(discriminator="kind")],
    ):
        correction = isinstance(body, CorrectInput)
        return service.submit(token, ledger_id, entry_id, ChangeSubmission(
            kind=ChangeKind(body.kind), revision=body.revision, reason=body.reason,
            draft=body.to_draft() if correction else None,
            receipts=body.receipt_selection() if correction else ReceiptSelection(),
        ))

    @router.post("/entries/{entry_id}/changes/{change_id}/review", response_model=ChangeRequest)
    def decide(
        ledger_id: UUID, entry_id: UUID, change_id: UUID, token: SessionToken, body: DecideInput,
    ):
        return service.decide(token, ledger_id, entry_id, ChangeDecision(
            change_id=change_id, status=ChangeStatus(body.result), reason=body.reason,
        ))

    @router.get("/changes", response_model=ChangeQueue)
    def queue(
        ledger_id: UUID, token: SessionToken,
        offset: Annotated[int, Query(ge=0, le=MAX_INTEGER)] = 0,
    ):
        limit = 30
        items = service.queue(token, ledger_id, limit + 1, offset)
        next_offset = offset + limit if len(items) > limit else None
        return ChangeQueue(items=items[:limit], next_offset=next_offset)

    return router
