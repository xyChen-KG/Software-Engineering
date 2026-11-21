"""账本与成员 HTTP 接口；所有目标对象均在服务层重新鉴权。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query

from app.api.auth_routes import SessionToken
from app.api.schemas import (
    AuditResponse, InvitationInboxResponse, InvitationResponse, InviteRequest,
    LedgerCreate, LedgerResponse, LedgerUpdate,
    MemberResponse, NamedMemberResponse, RoleRequest, TransferRequest,
)
from app.services.ledgers import LedgerService
from app.models.search import LogFilters
from app.models.validation import MAX_INTEGER


def ledger_router(service: LedgerService) -> APIRouter:
    """路由仅负责输入输出，不从客户端角色字段作授权判断。"""
    router = APIRouter(tags=["账本与成员"])

    @router.get("/ledgers", response_model=list[LedgerResponse])
    def ledgers(token: SessionToken):
        return [LedgerResponse.from_access(access) for access in service.list_for_user(token)]

    @router.post("/ledgers", response_model=LedgerResponse, status_code=201)
    def create(body: LedgerCreate, token: SessionToken):
        return LedgerResponse.from_access(service.create(token, body.name, body.budget_cents))

    @router.get("/ledgers/{ledger_id}", response_model=LedgerResponse)
    def detail(ledger_id: UUID, token: SessionToken):
        return LedgerResponse.from_access(service.get(token, ledger_id))

    @router.patch("/ledgers/{ledger_id}", response_model=LedgerResponse)
    def update(ledger_id: UUID, body: LedgerUpdate, token: SessionToken):
        return LedgerResponse.from_access(service.update(
            token, ledger_id, body.model_dump(exclude_unset=True),
        ))

    @router.get("/ledgers/{ledger_id}/logs", response_model=list[AuditResponse])
    def logs(
        ledger_id: UUID, token: SessionToken, filters: Annotated[LogFilters, Query()],
    ):
        return [AuditResponse.from_event(event) for event in service.logs(
            token, ledger_id, filters,
        )]

    @router.get("/me/budget-alerts")
    def budget_alerts(token: SessionToken):
        with service.store.transaction(write=False) as repos:
            user = service.auth.authenticate(repos, token)
            return repos.budget_alerts.unread(user.id)

    @router.post("/me/budget-alerts/{alert_id}/read", status_code=204)
    def acknowledge(alert_id: Annotated[int, Path(ge=1, le=MAX_INTEGER)], token: SessionToken):
        with service.store.transaction() as repos:
            user = service.auth.authenticate(repos, token)
            repos.budget_alerts.acknowledge(user.id, alert_id)

    router.include_router(_member_router(service))
    router.include_router(_invitation_router(service))
    return router


def _member_router(service: LedgerService) -> APIRouter:
    router = APIRouter()

    @router.get("/ledgers/{ledger_id}/members", response_model=list[NamedMemberResponse])
    def members(ledger_id: UUID, token: SessionToken, include_exited: bool = False):
        return [NamedMemberResponse.from_info(info) for info in service.members(
            token, ledger_id, include_exited=include_exited,
        )]

    @router.patch("/ledgers/{ledger_id}/members/{member_id}", response_model=MemberResponse)
    def role(ledger_id: UUID, member_id: UUID, body: RoleRequest, token: SessionToken):
        return service.change_role(token, ledger_id, member_id, body.role, body.reason)

    @router.post("/ledgers/{ledger_id}/transfer-owner", response_model=MemberResponse)
    def transfer(ledger_id: UUID, body: TransferRequest, token: SessionToken):
        return service.transfer(token, ledger_id, body.member_id, body.reason)

    @router.post("/ledgers/{ledger_id}/leave", response_model=MemberResponse)
    def leave(ledger_id: UUID, token: SessionToken):
        return service.leave(token, ledger_id)

    @router.delete("/ledgers/{ledger_id}/members/{member_id}", response_model=MemberResponse)
    def remove(ledger_id: UUID, member_id: UUID, token: SessionToken):
        return service.remove(token, ledger_id, member_id)

    return router


def _invitation_router(service: LedgerService) -> APIRouter:
    router = APIRouter()

    @router.post("/ledgers/{ledger_id}/invitations", response_model=InvitationResponse,
                 status_code=201)
    def invite(ledger_id: UUID, body: InviteRequest, token: SessionToken):
        return service.invite(token, ledger_id, body.username, body.role)

    @router.get("/ledgers/{ledger_id}/invitations", response_model=list[InvitationResponse])
    def invitations(ledger_id: UUID, token: SessionToken):
        return service.invitations(token, ledger_id)

    @router.delete("/ledgers/{ledger_id}/invitations/{invitation_id}",
                   response_model=InvitationResponse)
    def revoke_invitation(ledger_id: UUID, invitation_id: UUID, token: SessionToken):
        return service.revoke_invitation(token, ledger_id, invitation_id)

    @router.get("/invitations", response_model=list[InvitationInboxResponse])
    def my_invitations(token: SessionToken):
        return [InvitationInboxResponse.from_info(info) for info in service.my_invitations(token)]

    @router.post("/invitations/{invitation_id}/accept", response_model=MemberResponse)
    def accept(invitation_id: UUID, token: SessionToken):
        return service.accept(token, invitation_id)

    return router
