"""登记线下结清及导出个人账务。"""

from app.storage.common import encode_time
from app.errors import ConflictError, ValidationError


class SettlementService:
    """付款人和收款人的余额、日志在同一个事务内检查和写入。"""

    def __init__(self, ledgers):
        self.ledgers = ledgers

    def statement(self, token, ledger_id):
        """账本内所有成员可核对结算清单。"""
        with self.ledgers.store.transaction(write=False) as repos:
            self.ledgers.scope(repos, token, ledger_id)
            return {"members": repos.settlements.balances(ledger_id),
                    "payments": repos.settlements.history(ledger_id),
                    "balance": repos.entry_workflow.totals(ledger_id).balance_cents}

    def personal(self, token, ledger_id):
        """按登录身份导出，退出后会失去访问权。"""
        with self.ledgers.store.transaction(write=False) as repos:
            scope = self.ledgers.scope(repos, token, ledger_id)
            member = scope.membership.id
            balances = repos.settlements.balances(ledger_id)
            return {"ledger": scope.ledger.name, "member": balances[str(member)],
                    "currency_unit": "人民币分",
                    "member_names": {key: item["name"] for key, item in balances.items()},
                    "entries": repos.settlements.personal_entries(ledger_id, member),
                    "payments": repos.settlements.history(ledger_id, member),
                    "pending": repos.ledgers.pending_count(ledger_id, member)}

    def record(self, token, ledger_id, payment):
        """当前应补方付给应收方，可分次结清。"""
        with self.ledgers.store.transaction() as repos:
            scope = self.ledgers.scope(repos, token, ledger_id, owner=True)
            actor = scope.membership.id
            existing = repos.settlements.existing(payment.request_id)
            if existing:
                expected = (str(ledger_id), str(actor), str(payment.payer),
                            str(payment.payee), payment.amount)
                fields = ("ledger_id", "actor", "payer", "payee", "amount")
                same_time = existing["paid_at"] == (
                    encode_time(payment.paid_at) if payment.paid_at else None)
                if tuple(existing[k] for k in fields) != expected or not same_time:
                    raise ConflictError("同一登记编号的内容不一致")
                return existing
            if scope.ledger.archived:
                raise ConflictError("归档账本须先恢复")
            members = repos.settlements.balances(ledger_id)
            payer, payee = members.get(str(payment.payer)), members.get(str(payment.payee))
            if not payer or not payee or payer == payee:
                raise ValidationError("请选择本账本的不同成员")
            if payment.amount > min(-payer["remaining"], payee["remaining"]):
                raise ConflictError("金额超过当前应收或应补，请刷新结算清单")
            now = self.ledgers.auth.clock()
            if payment.paid_at and payment.paid_at > now:
                raise ValidationError("线下付款时间不能晚于现在")
            repos.settlements.add(ledger_id, actor, payment, now)
            self.ledgers.log(repos, scope.membership, payment.request_id, "settlement.recorded", {
                "payer": payment.payer, "payee": payment.payee, "amount_cents": payment.amount,
                "paid_at": encode_time(payment.paid_at or now),
            })
            return repos.settlements.existing(payment.request_id)
