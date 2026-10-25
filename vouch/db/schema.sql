-- Vouch system of record: the event vendor payables tables (spec §4.1).
-- Rows are loaded once, as they stand on the scenario's as_of date (later rows are never loaded: D19),
-- and are never changed afterwards. Amounts are exact decimal text; JSON columns hold structured terms.
-- Each table keeps any extra fields a scenario supplies in `extra` (JSON), so nothing given is dropped.

CREATE TABLE scenario (
    key    TEXT PRIMARY KEY,                    -- as_of | scenario_id | title
    value  TEXT NOT NULL
);

CREATE TABLE entities (id TEXT PRIMARY KEY, name TEXT NOT NULL, currency TEXT NOT NULL, extra TEXT);
CREATE TABLE cost_centers (id TEXT PRIMARY KEY, name TEXT NOT NULL, extra TEXT);
CREATE TABLE people (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, entity TEXT, email TEXT, extra TEXT
);
CREATE TABLE delegations (
    id TEXT PRIMARY KEY, from_person TEXT NOT NULL, to_person TEXT NOT NULL, role TEXT NOT NULL,
    valid_from TEXT, valid_to TEXT, extra TEXT
);
CREATE TABLE events (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, entity TEXT NOT NULL, cost_center TEXT, owner TEXT,
    start_date TEXT NOT NULL, end_date TEXT NOT NULL,
    status TEXT,                                -- as loaded; Vouch derives held/planned/cancelled from dates and evidence
    extra TEXT
);
CREATE TABLE vendors (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, currency TEXT, email_domain TEXT, phone_on_file TEXT,
    enhanced_review_until TEXT, category TEXT, extra TEXT
);
CREATE TABLE vendor_bank_accounts (
    vendor TEXT NOT NULL, account_last4 TEXT NOT NULL, active_from TEXT, active_to TEXT, extra TEXT,
    PRIMARY KEY (vendor, account_last4)
);
CREATE TABLE vendor_master_changes (
    id TEXT PRIMARY KEY, vendor TEXT NOT NULL, field TEXT, new_value TEXT, requested_via TEXT,
    requested_by TEXT, requested_at TEXT, callback_number TEXT, callback_by TEXT, callback_at TEXT,
    changed_by TEXT, approved_by TEXT, extra TEXT
);
CREATE TABLE contracts (
    id TEXT PRIMARY KEY, vendor TEXT NOT NULL, event TEXT, entity TEXT, signed_date TEXT, payment_terms TEXT,
    extra TEXT
);
CREATE TABLE contract_clauses (
    clause_id TEXT PRIMARY KEY, contract TEXT NOT NULL, type TEXT, params TEXT NOT NULL, text TEXT, extra TEXT
);
CREATE TABLE purchase_orders (
    id TEXT PRIMARY KEY, vendor TEXT NOT NULL, event TEXT, contract TEXT, requester TEXT, amount TEXT,
    currency TEXT, extra TEXT
);
CREATE TABLE po_lines (
    id TEXT PRIMARY KEY, po TEXT NOT NULL, clause_ref TEXT, description TEXT, amount TEXT, extra TEXT
);
CREATE TABLE invoices (
    id TEXT PRIMARY KEY, vendor TEXT, entity TEXT, po TEXT, event TEXT, contract TEXT, invoice_number TEXT,
    invoice_date TEXT, received_at TEXT, received_via TEXT, currency TEXT, total TEXT, notes TEXT,
    raw_text TEXT, extra TEXT
);
CREATE TABLE invoice_lines (
    id TEXT PRIMARY KEY, invoice TEXT NOT NULL, line_no INTEGER, description TEXT, item TEXT,
    kind TEXT,                                  -- item | service_charge | tax | vat | deposit | deposit_credit | fee
    qty TEXT, unit_price TEXT, amount TEXT, clause_ref TEXT, po_line TEXT, service_date TEXT,
    detail TEXT,                                -- JSON, e.g. {"crew": 6, "hours": 4.0}
    extra TEXT
);
CREATE TABLE evidence (
    id TEXT PRIMARY KEY, event TEXT, type TEXT NOT NULL, date TEXT, value TEXT NOT NULL, signed_by TEXT,
    extra TEXT
);
CREATE TABLE emails (
    id TEXT PRIMARY KEY, from_address TEXT, from_domain TEXT, to_address TEXT, received_at TEXT,
    subject TEXT, body TEXT, extra TEXT
);
CREATE TABLE payments (
    id TEXT PRIMARY KEY, invoice TEXT, amount TEXT, paid_at TEXT, account_last4 TEXT, extra TEXT
);
CREATE TABLE policies (id TEXT PRIMARY KEY, value TEXT, text TEXT, note TEXT, extra TEXT);
CREATE TABLE period_rates (
    pair TEXT NOT NULL, period TEXT NOT NULL, rate TEXT NOT NULL, extra TEXT, PRIMARY KEY (pair, period)
);
CREATE TABLE vendor_history (
    id TEXT PRIMARY KEY, vendor TEXT NOT NULL, event TEXT, kind TEXT NOT NULL, amount TEXT, date TEXT,
    note TEXT, extra TEXT
);

CREATE TABLE periods (
    period      TEXT PRIMARY KEY,               -- 'YYYY-MM'
    close_date  TEXT NOT NULL,                  -- the 5th of the next month
    status      TEXT NOT NULL CHECK (status IN ('open', 'locked'))
);

-- Loaded rows are stored as they stood on the as_of date and are never changed. In particular
-- vendor master data and bank accounts are read-only to everyone in Vouch (D20).
CREATE TRIGGER vendors_read_only BEFORE UPDATE ON vendors
BEGIN SELECT RAISE(ABORT, 'vendor master data is read-only in Vouch'); END;
CREATE TRIGGER vendors_no_delete BEFORE DELETE ON vendors
BEGIN SELECT RAISE(ABORT, 'vendor master data is read-only in Vouch'); END;
CREATE TRIGGER bank_accounts_read_only BEFORE UPDATE ON vendor_bank_accounts
BEGIN SELECT RAISE(ABORT, 'vendor bank accounts are read-only in Vouch'); END;
CREATE TRIGGER bank_accounts_no_delete BEFORE DELETE ON vendor_bank_accounts
BEGIN SELECT RAISE(ABORT, 'vendor bank accounts are read-only in Vouch'); END;
CREATE TRIGGER master_changes_read_only BEFORE UPDATE ON vendor_master_changes
BEGIN SELECT RAISE(ABORT, 'vendor master changes are read-only in Vouch'); END;
CREATE TRIGGER invoices_as_received BEFORE UPDATE ON invoices
BEGIN SELECT RAISE(ABORT, 'invoices are stored as received and cannot be changed'); END;
CREATE TRIGGER invoice_lines_as_received BEFORE UPDATE ON invoice_lines
BEGIN SELECT RAISE(ABORT, 'invoice lines are stored as received and cannot be changed'); END;
CREATE TRIGGER evidence_as_received BEFORE UPDATE ON evidence
BEGIN SELECT RAISE(ABORT, 'evidence is stored as received and cannot be changed'); END;
CREATE TRIGGER emails_as_received BEFORE UPDATE ON emails
BEGIN SELECT RAISE(ABORT, 'emails are stored as received and cannot be changed'); END;
CREATE TRIGGER payments_history_only BEFORE INSERT ON payments
WHEN (SELECT value FROM scenario WHERE key = 'loading') IS NULL
BEGIN SELECT RAISE(ABORT, 'payments are history only; Vouch never creates them'); END;
CREATE TRIGGER payments_no_update BEFORE UPDATE ON payments
BEGIN SELECT RAISE(ABORT, 'payments are history only'); END;
CREATE TRIGGER clauses_as_signed BEFORE UPDATE ON contract_clauses
BEGIN SELECT RAISE(ABORT, 'contract clauses are stored as signed and cannot be changed'); END;
