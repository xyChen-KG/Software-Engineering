"""成员垫付、分摊及线下结清明细。"""

from app.storage.common import Repository, encode_time
from app.storage.entry_workflow import CURRENT_CONTENT


class SettlementRepository(Repository):
    """按正式版本重新计算权益，再扣除已经实际发生的转付。"""

    def balances(self, ledger_id):
        """包含退出成员，并使用整数分计算应收应补。"""
        key = str(ledger_id)
        members = {row["id"]: {**dict(row), "advanced": 0, "share": 0, "paid": 0, "received": 0}
                   for row in self._execute(
                       "SELECT m.id,u.name,m.status,m.role FROM memberships m JOIN users u "
                       "ON u.id=m.user_id WHERE m.ledger_id=? ORDER BY m.joined_at,m.id", (key,))}
        valid = " WHERE e.ledger_id=? AND e.status='approved' AND e.kind='expense'"
        for member, amount in self._execute(
            "SELECT v.payer_member_id,v.amount_cents " + CURRENT_CONTENT + valid, (key,),
        ):
            members[member]["advanced"] += amount
        for member, amount in self._execute(
            "SELECT s.member_id,s.amount_cents FROM entries e JOIN splits s "
            "ON s.entry_id=e.id AND s.version_number=e.current_version" + valid, (key,),
        ):
            members[member]["share"] += amount
        for payer, payee, amount in self._execute(
            "SELECT payer,payee,amount FROM settlements WHERE ledger_id=?", (key,),
        ):
            members[payer]["paid"] += amount
            members[payee]["received"] += amount
        for member in members.values():
            member["net"] = member["advanced"] - member["share"]
            member["remaining"] = member["net"] + member["paid"] - member["received"]
        return members

    def history(self, ledger_id, member_id=None):
        """读取实际结清记录，可限制为个人来往。"""
        sql = "SELECT * FROM settlements WHERE ledger_id=?"
        args = [str(ledger_id)]
        if member_id:
            sql += " AND (payer=? OR payee=?)"
            args.extend([str(member_id)] * 2)
        return [dict(row) for row in self._execute(sql + " ORDER BY created_at DESC,id", args)]

    def personal_entries(self, ledger_id, member_id):
        """列出本人提交、垫付、收款或参与分摊的每笔账及个人承担额。"""
        key = str(member_id)
        return [dict(row) for row in self._execute(
            "SELECT e.id,e.kind,e.status,v.business_date,v.category,v.purpose,v.amount_cents,"
            "v.payer_member_id,e.submitter_member_id,coalesce(s.amount_cents,0) AS own_share "
            + CURRENT_CONTENT +
            " LEFT JOIN splits s ON s.entry_id=e.id AND s.version_number=e.current_version "
            " AND s.member_id=? WHERE e.ledger_id=? AND (v.payer_member_id=? OR "
            "e.submitter_member_id=? OR s.member_id IS NOT NULL) "
            "ORDER BY v.business_date DESC,e.id",
            (key, str(ledger_id), key, key),
        )]

    def existing(self, request_id):
        """判断超时重试是否已登记。"""
        row = self._execute("SELECT * FROM settlements WHERE id=?", (str(request_id),)).fetchone()
        return dict(row) if row else None

    def add(self, ledger_id, actor, payment, now):
        """登记经办人和实际登记时间。"""
        self._execute("INSERT INTO settlements(id,ledger_id,payer,payee,actor,amount,created_at,"
                      "paid_at) VALUES(?,?,?,?,?,?,?,?)", (
            str(payment.request_id), str(ledger_id), str(payment.payer), str(payment.payee),
            str(actor), payment.amount, encode_time(now),
            encode_time(payment.paid_at) if payment.paid_at else None,
        ))
