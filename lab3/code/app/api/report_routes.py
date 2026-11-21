"""查询统计与账本报表的接口。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import AwareDatetime

from app.api.auth_routes import SessionToken
from app.api.entry_routes import Amount
from app.api.schemas import RequestModel
from app.services.settlements import SettlementService
from app.models.search import SearchFilters
from app.services.ledgers import LedgerService


class Payment(RequestModel):
    """一次已在线下发生的付款。"""

    request_id: UUID
    payer: UUID
    payee: UUID
    amount: Amount
    paid_at: AwareDatetime | None = None


def report_router(service: LedgerService) -> APIRouter:
    """每次查询都检查当前账本成员身份。"""
    router = APIRouter(prefix="/ledgers/{ledger_id}", tags=["账本报表"])

    @router.get("/search")
    def search(ledger_id: UUID, token: SessionToken, filters: Annotated[SearchFilters, Query()]):
        with service.store.transaction(write=False) as repos:
            service.scope(repos, token, ledger_id)
            return repos.reporting.search(ledger_id, filters)

    @router.get("/budget")
    def budget(ledger_id: UUID, token: SessionToken):
        with service.store.transaction(write=False) as repos:
            scope = service.scope(repos, token, ledger_id)
            return repos.reporting.budget(scope.ledger)

    @router.get("/overview")
    def overview(ledger_id: UUID, token: SessionToken):
        with service.store.transaction(write=False) as repos:
            scope = service.scope(repos, token, ledger_id)
            return {**repos.reporting.budget(scope.ledger),
                    "recent": repos.reporting.recent(ledger_id),
                    "balances": {key: item["remaining"] for key, item
                                 in repos.settlements.balances(ledger_id).items()}}

    settlements = SettlementService(service)

    @router.get("/settlements")
    def statement(ledger_id: UUID, token: SessionToken):
        return settlements.statement(token, ledger_id)

    @router.post("/settlements", status_code=201)
    def record(ledger_id: UUID, token: SessionToken, body: Payment):
        return settlements.record(token, ledger_id, body)

    @router.get("/personal-statement")
    def personal(ledger_id: UUID, token: SessionToken):
        return settlements.personal(token, ledger_id)

    return router
