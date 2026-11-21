"""预算跨档时保存给当时在册负责人的提醒，超支时也通知审核员。"""

from app.storage.common import Repository, encode_time


def budget_level(expense, budget):
    """100% 仍属于黄色区间；空预算和零预算不做比例提醒。"""
    if not budget or expense * 100 < budget * 80:
        return "none"
    return "over" if expense > budget else "warning"


ELIGIBLE = """m.status='active' AND (m.role='owner' OR (m.role='reviewer' AND a.level='over'))"""
INBOX = """ FROM budget_alerts a JOIN memberships m ON m.id=a.member_id
            JOIN ledgers l ON l.id=m.ledger_id WHERE m.user_id=? AND """ + ELIGIBLE


class BudgetAlerts(Repository):
    """每位收件人独立管理未读状态，每次触发保存当时的预算与支出。"""

    def publish(self, ledger_id, expense, budget, when, *, recipient=None):
        """与引发变化的操作在同一事务中选取收件人并写入快照。"""
        level = budget_level(expense, budget)
        if level == "none":
            return
        target = " AND id=?" if recipient is not None else ""
        targets = (str(recipient),) if recipient is not None else ()
        self._execute(
            """INSERT INTO budget_alerts(member_id, level, expense, budget, created_at)
               SELECT id, ?, ?, ?, ? FROM memberships WHERE ledger_id=? AND status='active'
               AND (role='owner' OR (role='reviewer' AND ?='over'))""" + target,
            (level, str(expense), budget, encode_time(when), str(ledger_id), level, *targets),
        )

    def after_change(self, reporting, ledger, previous_budget, delta, when):
        """从有效支出的净变化还原先前档位，只在向上跨档时发送。"""
        if not ledger.budget_cents or ledger.archived:
            return
        overview = reporting.budget(ledger)
        level, spent = overview["level"], overview["expense"]
        previous = budget_level(spent - delta, previous_budget)
        if (level == "over" and previous != "over") or (level == "warning" and previous == "none"):
            self.publish(ledger.id, spent, ledger.budget_cents, when)

    def discard_ineligible(self, member_id):
        """降权和退出立即清除失去资格的旧提醒，再加入也不会重新出现。"""
        self._execute(
            """DELETE FROM budget_alerts AS a WHERE member_id=? AND NOT EXISTS (
               SELECT 1 FROM memberships m WHERE m.id=a.member_id AND """ + ELIGIBLE + ")",
            (str(member_id),),
        )

    def unread(self, user_id):
        """先展示最新的 25 条，逐条确认后可继续读取较早提醒。"""
        parameters = (str(user_id),)
        count = self._execute("SELECT COUNT(*)" + INBOX, parameters).fetchone()[0]
        rows = self._execute(
            "SELECT a.id, l.name AS ledger_name, a.level, a.budget, a.expense, a.created_at" +
            INBOX + " ORDER BY a.id DESC LIMIT 25", parameters,
        )
        return {"count": count, "items": [dict(row) | {"expense": int(row["expense"])}
                                          for row in rows]}

    def acknowledge(self, user_id, alert_id):
        """只移除自己的未读提醒，重复确认不会修改其他收件人的数据。"""
        self._execute("DELETE FROM budget_alerts WHERE id IN (SELECT a.id" + INBOX +
                      " AND a.id=?)", (str(user_id), alert_id))
