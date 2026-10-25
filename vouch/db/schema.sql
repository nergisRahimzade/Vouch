-- Vouch system of record: the German event tax tables (spec §4.1).
-- Rows are loaded once, as they stand on the scenario's as_of date (later rows are never loaded: D19).
-- Amounts are exact decimal text; JSON columns hold structured values. Each table keeps any extra fields
-- a scenario supplies in `extra` (JSON), so nothing given is dropped.

CREATE TABLE scenario (
    key    TEXT PRIMARY KEY,                    -- as_of | scenario_id | title
    value  TEXT NOT NULL
);

CREATE TABLE entities (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, currency TEXT NOT NULL, state TEXT, fiscal_year TEXT, extra TEXT
);
CREATE TABLE cost_centers (id TEXT PRIMARY KEY, name TEXT NOT NULL, members TEXT, extra TEXT);
CREATE TABLE org_units (id TEXT PRIMARY KEY, name TEXT, members TEXT, extra TEXT);
CREATE TABLE employees (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, cost_center TEXT, is_manager INTEGER NOT NULL DEFAULT 0, extra TEXT
);
CREATE TABLE people (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, employee_id TEXT, email TEXT, extra TEXT
);
CREATE TABLE delegations (
    id TEXT PRIMARY KEY, from_person TEXT NOT NULL, to_person TEXT NOT NULL, role TEXT NOT NULL,
    valid_from TEXT, valid_to TEXT, extra TEXT
);
CREATE TABLE business_partners (id TEXT PRIMARY KEY, name TEXT NOT NULL, company TEXT, extra TEXT);
CREATE TABLE vendors (id TEXT PRIMARY KEY, name TEXT NOT NULL, extra TEXT);
CREATE TABLE events (
    id TEXT PRIMARY KEY, name TEXT, type_declared TEXT, date TEXT NOT NULL, end_date TEXT, organizer TEXT,
    open_to TEXT,                               -- all | an org unit / cost center id | a list (JSON) | managers
    purpose TEXT, status TEXT, location TEXT, host TEXT, cost_center TEXT,
    exceptional_assignment INTEGER,             -- 1: a working meal during an exceptional assignment
    extra TEXT
);
CREATE TABLE attendance (
    id TEXT PRIMARY KEY, event TEXT NOT NULL, person_id TEXT NOT NULL,
    person_kind TEXT NOT NULL CHECK (person_kind IN ('employee', 'companion', 'business_partner', 'external')),
    host_employee TEXT, extra TEXT
);
CREATE TABLE gift_distributions (
    id TEXT PRIMARY KEY, event TEXT, invoice TEXT, invoice_line TEXT, recipient TEXT, qty TEXT, unit_net TEXT,
    date TEXT, extra TEXT
);
CREATE TABLE invoices (
    id TEXT PRIMARY KEY, vendor TEXT, entity TEXT, bill_to TEXT, event_ref TEXT,
    event TEXT,                                 -- an event ID printed on the invoice, if any
    invoice_number TEXT, invoice_date TEXT, received_at TEXT, delivery_date TEXT, net TEXT, vat TEXT, gross TEXT,
    occasion TEXT, host TEXT, place TEXT, participants TEXT,     -- hospitality documentation on the receipt
    notes TEXT, raw_text TEXT, extra TEXT
);
CREATE TABLE invoice_lines (
    id TEXT PRIMARY KEY, invoice TEXT NOT NULL, line_no INTEGER,
    category TEXT,                              -- catering | venue | program | transport | gift | promo_item | package
    description TEXT, net TEXT, vat_rate TEXT, vat TEXT, gross TEXT, qty TEXT, unit_net TEXT, recipient TEXT,
    extra TEXT
);
CREATE TABLE emails (
    id TEXT PRIMARY KEY, from_address TEXT, to_address TEXT, received_at TEXT, subject TEXT, body TEXT, extra TEXT
);
CREATE TABLE elections (
    id TEXT PRIMARY KEY, year INTEGER NOT NULL, scope TEXT NOT NULL, regime TEXT NOT NULL, elected INTEGER NOT NULL,
    decided_by TEXT, decided_at TEXT, extra TEXT
);
CREATE TABLE payroll_exports (
    id TEXT PRIMARY KEY, period TEXT NOT NULL, employee TEXT NOT NULL, event TEXT, taxable_amount TEXT NOT NULL,
    method TEXT NOT NULL CHECK (method IN ('flat_40', 'individual')),
    status TEXT NOT NULL DEFAULT 'accepted',    -- draft (Vouch) | accepted (payroll) | rejected
    origin TEXT NOT NULL DEFAULT 'history',     -- history | vouch
    corrects TEXT,                              -- the earlier item a delta corrects
    extra TEXT
);
CREATE TABLE recipient_notices (
    id TEXT PRIMARY KEY, recipient TEXT NOT NULL, year INTEGER NOT NULL, items TEXT, status TEXT NOT NULL, extra TEXT
);
CREATE TABLE rules (
    rule_id TEXT NOT NULL, version INTEGER NOT NULL, valid_from TEXT, valid_to TEXT, value TEXT, params TEXT,
    text TEXT, basis TEXT, source_url TEXT, checked TEXT, extra TEXT, PRIMARY KEY (rule_id, version)
);
CREATE TABLE policies (id TEXT PRIMARY KEY, text TEXT NOT NULL, owner TEXT, extra TEXT);

CREATE TABLE periods (
    period      TEXT PRIMARY KEY,               -- 'YYYY-MM'
    close_date  TEXT NOT NULL,                  -- the 10th of the next month
    status      TEXT NOT NULL CHECK (status IN ('open', 'locked'))
);

-- Elections and rules are decided by people and are read-only to Vouch (D25).
CREATE TRIGGER elections_read_only BEFORE UPDATE ON elections
BEGIN SELECT RAISE(ABORT, 'the §37b election is decided by people and is read-only in Vouch'); END;
CREATE TRIGGER elections_no_delete BEFORE DELETE ON elections
BEGIN SELECT RAISE(ABORT, 'the §37b election is decided by people and is read-only in Vouch'); END;
CREATE TRIGGER elections_no_insert BEFORE INSERT ON elections
WHEN (SELECT value FROM scenario WHERE key = 'loading') IS NULL
BEGIN SELECT RAISE(ABORT, 'the §37b election is decided by people and is read-only in Vouch'); END;
CREATE TRIGGER rules_read_only BEFORE UPDATE ON rules
BEGIN SELECT RAISE(ABORT, 'rules are seeded with their sources and are read-only'); END;
-- What arrived is stored as it arrived.
CREATE TRIGGER invoices_as_received BEFORE UPDATE ON invoices
BEGIN SELECT RAISE(ABORT, 'invoices are stored as received and cannot be changed'); END;
CREATE TRIGGER invoice_lines_as_received BEFORE UPDATE ON invoice_lines
BEGIN SELECT RAISE(ABORT, 'invoice lines are stored as received and cannot be changed'); END;
CREATE TRIGGER attendance_as_received BEFORE UPDATE ON attendance
BEGIN SELECT RAISE(ABORT, 'attendance is stored as received and cannot be changed'); END;
CREATE TRIGGER emails_as_received BEFORE UPDATE ON emails
BEGIN SELECT RAISE(ABORT, 'emails are stored as received and cannot be changed'); END;
-- Vouch drafts payroll exports; only payroll accepts them, and an accepted row is final.
CREATE TRIGGER payroll_exports_drafts_only BEFORE INSERT ON payroll_exports
WHEN (SELECT value FROM scenario WHERE key = 'loading') IS NULL AND NEW.status <> 'draft'
BEGIN SELECT RAISE(ABORT, 'Vouch only drafts payroll exports; payroll accepts them'); END;
CREATE TRIGGER payroll_exports_accepted_final BEFORE UPDATE ON payroll_exports
WHEN OLD.status = 'accepted'
BEGIN SELECT RAISE(ABORT, 'an accepted payroll export is final; draft a delta instead'); END;
