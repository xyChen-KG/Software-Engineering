"""组合查询和按当前有效版本计算的统计。"""

import sqlite3
from collections import defaultdict
from uuid import UUID

from app.models.search import SearchFilters
from app.storage.budget_alerts import budget_level
from app.storage.common import Repository
from app.storage.entry_workflow import CURRENT_CONTENT


def _selection(ledger_id, filters):
    terms = ["e.ledger_id=?"]
    values = [str(ledger_id)]
    for clause, value in (
        ("e.kind=?", filters.kind), ("e.status=?", filters.status),
        ("v.business_date>=?", filters.start), ("v.business_date<=?", filters.end),
        ("v.category=?", filters.category.strip() or None),
        ("v.amount_cents>=?", filters.minimum), ("v.amount_cents<=?", filters.maximum),
        ("instr(lower(v.purpose),lower(?))>0", filters.keyword.strip() or None),
    ):
        if value is not None:
            terms.append(clause)
            values.append(value.isoformat() if hasattr(value, "isoformat") else value)
    if filters.member_id:
        terms.append("(e.submitter_member_id=? OR v.payer_member_id=?)")
        values.extend([str(filters.member_id)] * 2)
    return " WHERE " + " AND ".join(terms), values


class ReportingRepository(Repository):
    """同一事务内返回分页数据和完整筛选范围的汇总。"""

    def budget(self, ledger, exclude=None):
        """已通过金额决定预警等级，待审支出单独计算。"""
        totals = {("approved", "expense"): 0, ("approved", "income"): 0, ("pending", "expense"): 0}
        for status, kind, amount in self._execute(
            "SELECT e.status,e.kind,v.amount_cents " + CURRENT_CONTENT +
            " WHERE e.ledger_id=? AND e.status IN ('approved','pending') AND e.id!=?",
            (str(ledger.id), str(exclude or "")),
        ):
            if (status, kind) in totals:
                totals[status, kind] += amount
        expense = totals["approved", "expense"]
        budget = ledger.budget_cents
        level = budget_level(expense, budget)
        return {"budget": budget, "expense": expense, "income": totals["approved", "income"],
                "pending": totals["pending", "expense"], "level": level,
                "excess": max(0, expense - budget) if budget else 0}

    def search(self, ledger_id: UUID, filters: SearchFilters) -> dict:
        """按发生日期倒序显示，使用分组小计加总整段筛选范围。"""
        where, args = _selection(ledger_id, filters)
        rows = self._page(where, args, filters.limit + 1, filters.offset)
        groups = self._groups(where, args)
        categories, members = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
        sums = [0, 0]
        for kind, category, payer, amount in groups:
            index = int(kind == "expense")
            categories[category][index] += amount
            members[payer][index] += amount
            sums[index] += amount
        return {
            "items": [dict(item) for item in rows[:filters.limit]],
            "next_offset": filters.offset + filters.limit if len(rows) > filters.limit else None,
            "income": sums[0], "expense": sums[1], "balance": sums[0] - sums[1],
            "categories": dict(sorted(categories.items())), "members": dict(members),
        }

    def recent(self, ledger_id):
        """账本首页显示最近三笔，包含待审记录。"""
        rows = self._page(" WHERE e.ledger_id=?", [str(ledger_id)], 3, 0)
        return [dict(row) for row in rows]

    def _page(self, where, args, limit, offset):
        return self._execute(
            "SELECT e.id,e.kind,e.status,v.amount_cents,v.business_date,v.category,v.purpose,"
            "u.name AS submitter " + CURRENT_CONTENT +
            " JOIN memberships m ON m.id=e.submitter_member_id JOIN users u ON u.id=m.user_id "
            + where + " ORDER BY v.business_date DESC,e.created_at DESC,e.id LIMIT ? OFFSET ?",
            (*args, limit, offset),
        ).fetchall()

    def _groups(self, where, args):
        fields = "e.kind,v.category,v.payer_member_id"
        source = CURRENT_CONTENT + where + " AND e.status='approved'"
        try:
            return self._execute("SELECT " + fields + ",SUM(v.amount_cents) " + source
                                 + " GROUP BY " + fields, args).fetchall()
        except sqlite3.OperationalError as exc:
            if "integer overflow" not in str(exc):
                raise
            # 极大金额由 Python 任意精度整数汇总，普通查询只传输各分组小计。
            return self._execute("SELECT " + fields + ",v.amount_cents " + source, args)
