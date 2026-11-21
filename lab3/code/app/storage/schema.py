"""数据库按版本迁移，DDL 和版本号在同一事务中保存。"""

SCHEMA_VERSION = 8
TABLES_V1 = frozenset({
    "users", "ledgers", "memberships", "entries", "entry_versions", "splits", "audit_events",
})
TABLES_V2 = TABLES_V1 | {"accounts", "sessions", "invitations"}
TABLES_V3 = TABLES_V2 | {"entry_reviews"}
TABLES_V4 = TABLES_V3 | {"entry_changes", "receipt_files", "version_receipts", "change_receipts"}
TABLES_V7 = TABLES_V4 | {"settlements"}
TABLES = TABLES_V7 | {"budget_alerts"}

STATEMENTS = (
    """
    CREATE TABLE users (
        id TEXT PRIMARY KEY NOT NULL,
        name TEXT NOT NULL CHECK(length(trim(name)) > 0),
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE ledgers (
        id TEXT PRIMARY KEY NOT NULL,
        name TEXT NOT NULL CHECK(length(trim(name)) > 0),
        budget_cents INTEGER CHECK(budget_cents IS NULL OR
            (typeof(budget_cents) = 'integer' AND budget_cents >= 0)),
        archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0, 1)),
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE memberships (
        id TEXT PRIMARY KEY NOT NULL,
        user_id TEXT NOT NULL REFERENCES users(id),
        ledger_id TEXT NOT NULL REFERENCES ledgers(id),
        role TEXT NOT NULL CHECK(role IN ('owner', 'reviewer', 'member')),
        status TEXT NOT NULL CHECK(status IN ('active', 'exited')),
        joined_at TEXT NOT NULL,
        UNIQUE(ledger_id, user_id),
        UNIQUE(id, ledger_id)
    )
    """,
    """
    CREATE UNIQUE INDEX one_active_owner ON memberships(ledger_id)
    WHERE role = 'owner' AND status = 'active'
    """,
    """
    CREATE TABLE entries (
        id TEXT PRIMARY KEY NOT NULL,
        ledger_id TEXT NOT NULL REFERENCES ledgers(id),
        submitter_member_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('income', 'expense')),
        request_id TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK(status IN
            ('pending', 'approved', 'rejected', 'withdrawn', 'voided')),
        current_version INTEGER NOT NULL CHECK(
            typeof(current_version) = 'integer' AND current_version >= 1),
        revision INTEGER NOT NULL CHECK(typeof(revision) = 'integer' AND revision >= 1),
        original_expense_id TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(id, ledger_id),
        CHECK(original_expense_id IS NULL OR
            (kind = 'income' AND original_expense_id != id)),
        FOREIGN KEY(submitter_member_id, ledger_id) REFERENCES memberships(id, ledger_id),
        FOREIGN KEY(original_expense_id, ledger_id) REFERENCES entries(id, ledger_id),
        FOREIGN KEY(id, current_version) REFERENCES entry_versions(entry_id, number)
            DEFERRABLE INITIALLY DEFERRED
    )
    """,
    """
    CREATE TABLE entry_versions (
        entry_id TEXT NOT NULL,
        ledger_id TEXT NOT NULL,
        number INTEGER NOT NULL CHECK(typeof(number) = 'integer' AND number >= 1),
        amount_cents INTEGER NOT NULL CHECK(typeof(amount_cents) = 'integer' AND amount_cents > 0),
        category TEXT NOT NULL CHECK(length(trim(category)) > 0),
        purpose TEXT NOT NULL CHECK(length(trim(purpose)) > 0),
        business_date TEXT NOT NULL,
        payer_member_id TEXT NOT NULL,
        no_receipt_reason TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        PRIMARY KEY(entry_id, number),
        UNIQUE(entry_id, number, ledger_id),
        FOREIGN KEY(entry_id, ledger_id) REFERENCES entries(id, ledger_id),
        FOREIGN KEY(payer_member_id, ledger_id) REFERENCES memberships(id, ledger_id)
    )
    """,
    """
    CREATE TABLE splits (
        entry_id TEXT NOT NULL,
        version_number INTEGER NOT NULL,
        ledger_id TEXT NOT NULL,
        member_id TEXT NOT NULL,
        amount_cents INTEGER NOT NULL CHECK(typeof(amount_cents) = 'integer' AND amount_cents >= 0),
        PRIMARY KEY(entry_id, version_number, member_id),
        FOREIGN KEY(entry_id, version_number, ledger_id)
            REFERENCES entry_versions(entry_id, number, ledger_id),
        FOREIGN KEY(member_id, ledger_id) REFERENCES memberships(id, ledger_id)
    )
    """,
    """
    CREATE TABLE audit_events (
        id TEXT PRIMARY KEY NOT NULL,
        ledger_id TEXT NOT NULL REFERENCES ledgers(id),
        actor_member_id TEXT NOT NULL,
        object_id TEXT NOT NULL,
        action TEXT NOT NULL CHECK(length(trim(action)) > 0),
        details TEXT NOT NULL CHECK(json_valid(details) AND json_type(details) = 'object'),
        created_at TEXT NOT NULL,
        FOREIGN KEY(actor_member_id, ledger_id) REFERENCES memberships(id, ledger_id)
    )
    """,
    "CREATE INDEX member_user ON memberships(user_id, status)",
    "CREATE INDEX entry_ledger_date ON entries(ledger_id, created_at, id)",
    "CREATE INDEX entry_ledger_status ON entries(ledger_id, status)",
    "CREATE INDEX log_ledger_date ON audit_events(ledger_id, created_at, id)",
    "CREATE INDEX log_actor ON audit_events(actor_member_id, created_at)",
    *(
        f"""CREATE TRIGGER immutable_{table}_{operation.lower()}
        BEFORE {operation} ON {table}
        BEGIN SELECT RAISE(ABORT, 'historical data is immutable'); END"""
        for table in ("entry_versions", "splits", "audit_events")
        for operation in ("UPDATE", "DELETE")
    ),
)

AUTH_STATEMENTS = (
    """
    CREATE TABLE accounts (
        user_id TEXT PRIMARY KEY NOT NULL REFERENCES users(id),
        username TEXT NOT NULL UNIQUE CHECK(username = lower(trim(username))
            AND length(username) BETWEEN 3 AND 32),
        password_hash TEXT NOT NULL CHECK(length(password_hash) > 0),
        failed_attempts INTEGER NOT NULL DEFAULT 0 CHECK(failed_attempts >= 0),
        locked_until TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY NOT NULL CHECK(length(token_hash) = 64),
        user_id TEXT NOT NULL REFERENCES accounts(user_id),
        issued_at TEXT NOT NULL,
        expires_at TEXT NOT NULL CHECK(expires_at > issued_at),
        revoked_at TEXT
    )
    """,
    "CREATE INDEX session_user ON sessions(user_id, expires_at)",
    """
    CREATE TABLE invitations (
        id TEXT PRIMARY KEY NOT NULL,
        ledger_id TEXT NOT NULL REFERENCES ledgers(id),
        target_user_id TEXT NOT NULL REFERENCES accounts(user_id),
        inviter_member_id TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('reviewer', 'member')),
        status TEXT NOT NULL CHECK(status IN ('pending', 'accepted', 'revoked')),
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL CHECK(expires_at > created_at),
        resolved_at TEXT,
        CHECK((status = 'pending' AND resolved_at IS NULL) OR
              (status != 'pending' AND resolved_at IS NOT NULL)),
        FOREIGN KEY(inviter_member_id, ledger_id) REFERENCES memberships(id, ledger_id)
    )
    """,
    """CREATE UNIQUE INDEX one_pending_invite ON invitations(ledger_id, target_user_id)
       WHERE status = 'pending'""",
    "CREATE INDEX invite_target ON invitations(target_user_id, status, expires_at)",
    "CREATE INDEX entry_submitter ON entries(ledger_id, submitter_member_id, status)",
)


REVIEW_STATEMENTS = (
    """
    CREATE TABLE entry_reviews (
        entry_id TEXT NOT NULL,
        version_number INTEGER NOT NULL,
        ledger_id TEXT NOT NULL,
        reviewer_member_id TEXT NOT NULL,
        result TEXT NOT NULL CHECK(result IN ('approved', 'rejected')),
        reason TEXT NOT NULL CHECK(result != 'rejected' OR length(trim(reason)) > 0),
        reviewed_at TEXT NOT NULL,
        PRIMARY KEY(entry_id, version_number),
        FOREIGN KEY(entry_id, version_number, ledger_id)
            REFERENCES entry_versions(entry_id, number, ledger_id),
        FOREIGN KEY(reviewer_member_id, ledger_id) REFERENCES memberships(id, ledger_id)
    )
    """,
    *(
        f"""CREATE TRIGGER immutable_entry_reviews_{operation.lower()}
        BEFORE {operation} ON entry_reviews
        BEGIN SELECT RAISE(ABORT, 'historical review is immutable'); END"""
        for operation in ("UPDATE", "DELETE")
    ),
)


CHANGE_STATEMENTS = (
    """
    CREATE TABLE entry_changes (
        id TEXT PRIMARY KEY NOT NULL,
        ledger_id TEXT NOT NULL,
        entry_id TEXT NOT NULL,
        base_version INTEGER NOT NULL,
        base_revision INTEGER NOT NULL CHECK(base_revision > 0),
        applicant_member_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('correct', 'void')),
        reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
        proposal TEXT CHECK(proposal IS NULL OR json_valid(proposal)),
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending', 'approved', 'rejected')),
        created_at TEXT NOT NULL,
        reviewer_member_id TEXT,
        review_reason TEXT NOT NULL DEFAULT '',
        reviewed_at TEXT,
        UNIQUE(id, entry_id, ledger_id),
        CHECK((kind = 'correct') = (proposal IS NOT NULL)),
        CHECK(reviewer_member_id != applicant_member_id),
        CHECK((status = 'pending' AND reviewed_at IS NULL AND reviewer_member_id IS NULL) OR
              (status != 'pending' AND reviewed_at IS NOT NULL AND reviewer_member_id IS NOT NULL)),
        CHECK(status != 'rejected' OR length(trim(review_reason)) > 0),
        FOREIGN KEY(entry_id, base_version, ledger_id)
            REFERENCES entry_versions(entry_id, number, ledger_id),
        FOREIGN KEY(applicant_member_id, ledger_id) REFERENCES memberships(id, ledger_id),
        FOREIGN KEY(reviewer_member_id, ledger_id) REFERENCES memberships(id, ledger_id)
    )
    """,
    "CREATE UNIQUE INDEX entry_one_open_change ON entry_changes(entry_id) WHERE status='pending'",
    "CREATE INDEX change_queue ON entry_changes(ledger_id, status, created_at, id)",
    "CREATE INDEX entry_change_history ON entry_changes(entry_id, created_at, id)",
    """
    CREATE TABLE receipt_files (
        id TEXT PRIMARY KEY NOT NULL,
        entry_id TEXT NOT NULL,
        ledger_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        mime TEXT NOT NULL CHECK(mime IN ('image/jpeg', 'image/png')),
        sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
        body BLOB NOT NULL CHECK(typeof(body) = 'blob' AND length(body) BETWEEN 1 AND 4194304),
        UNIQUE(id, entry_id, ledger_id),
        UNIQUE(entry_id, sha256, filename),
        FOREIGN KEY(entry_id, ledger_id) REFERENCES entries(id, ledger_id)
    )
    """,
    """
    CREATE TABLE version_receipts (
        entry_id TEXT NOT NULL,
        version_number INTEGER NOT NULL,
        ledger_id TEXT NOT NULL,
        receipt_id TEXT NOT NULL,
        PRIMARY KEY(entry_id, version_number, receipt_id),
        FOREIGN KEY(entry_id, version_number, ledger_id)
            REFERENCES entry_versions(entry_id, number, ledger_id),
        FOREIGN KEY(receipt_id, entry_id, ledger_id)
            REFERENCES receipt_files(id, entry_id, ledger_id)
    )
    """,
    """
    CREATE TABLE change_receipts (
        change_id TEXT NOT NULL,
        entry_id TEXT NOT NULL,
        ledger_id TEXT NOT NULL,
        receipt_id TEXT NOT NULL,
        PRIMARY KEY(change_id, receipt_id),
        FOREIGN KEY(change_id, entry_id, ledger_id)
            REFERENCES entry_changes(id, entry_id, ledger_id),
        FOREIGN KEY(receipt_id, entry_id, ledger_id)
            REFERENCES receipt_files(id, entry_id, ledger_id)
    )
    """,
    *(
        f"""CREATE TRIGGER immutable_{table}_{operation.lower()}
        BEFORE {operation} ON {table}
        BEGIN SELECT RAISE(ABORT, 'historical receipt data is immutable'); END"""
        for table in ("receipt_files", "version_receipts", "change_receipts")
        for operation in ("UPDATE", "DELETE")
    ),
)


def statements_for(version: int) -> tuple[str, ...]:
    """每个结构版本都有明确的前进迁移，版本一语句不再修改。"""
    migrations = {1: STATEMENTS, 2: AUTH_STATEMENTS, 3: REVIEW_STATEMENTS, 4: CHANGE_STATEMENTS}
    migrations[5] = (
        "ALTER TABLE entry_versions ADD COLUMN budget_reason TEXT NOT NULL DEFAULT ''",)
    migrations[6] = (
        """CREATE TABLE settlements (
             id TEXT PRIMARY KEY NOT NULL, ledger_id TEXT NOT NULL REFERENCES ledgers(id),
             payer TEXT NOT NULL, payee TEXT NOT NULL, actor TEXT NOT NULL,
             amount INTEGER NOT NULL CHECK(typeof(amount)='integer' AND amount>0),
             created_at TEXT NOT NULL, CHECK(payer<>payee),
             FOREIGN KEY(payer,ledger_id) REFERENCES memberships(id,ledger_id),
             FOREIGN KEY(payee,ledger_id) REFERENCES memberships(id,ledger_id),
             FOREIGN KEY(actor,ledger_id) REFERENCES memberships(id,ledger_id))""",
        "CREATE INDEX settlement_book ON settlements(ledger_id,created_at)",
        *(f"""CREATE TRIGGER immutable_settlements_{op} BEFORE {op} ON settlements
              BEGIN SELECT RAISE(ABORT, 'immutable settlement'); END"""
          for op in ("UPDATE", "DELETE")),
    )
    migrations[7] = ("ALTER TABLE settlements ADD COLUMN paid_at TEXT",)
    migrations[8] = (
        """CREATE TABLE budget_alerts(
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             member_id TEXT NOT NULL REFERENCES memberships(id),
             level TEXT NOT NULL CHECK(level IN ('warning','over')),
             expense TEXT NOT NULL, budget INTEGER NOT NULL CHECK(budget>0),
             created_at TEXT NOT NULL)""",
        "CREATE INDEX budget_alert_member ON budget_alerts(member_id,id)",
    )
    return migrations[version]


def tables_for(version: int) -> frozenset[str]:
    """迁移前按原版本检查完整性，不能将旧版本误判为缺表。"""
    return {1: TABLES_V1, 2: TABLES_V2, 3: TABLES_V3, 4: TABLES_V4, 5: TABLES_V4,
            6: TABLES_V7, 7: TABLES_V7, 8: TABLES}[version]
