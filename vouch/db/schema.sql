-- Vouch system of record. Every source file is kept exactly as it arrived.

CREATE TABLE documents (
    doc_id           INTEGER PRIMARY KEY,
    path             TEXT NOT NULL UNIQUE,      -- relative to the world root
    kind             TEXT NOT NULL,             -- hr_census, payroll_register, ..., pay_policy, inbox
    month            TEXT,                      -- 'YYYY-MM' for monthly files, NULL for standing documents
    entity           TEXT,                      -- US | UK | DE for per-entity files
    sha256           TEXT NOT NULL,
    manifest_sha256  TEXT,                      -- what the sender's manifest said; NULL if not listed
    size_bytes       INTEGER NOT NULL,
    row_count        INTEGER NOT NULL,
    content          BLOB NOT NULL,             -- the file's bytes as received
    loaded_at        TEXT NOT NULL
);

CREATE TABLE source_rows (
    doc_id   INTEGER NOT NULL REFERENCES documents(doc_id),
    row_no   INTEGER NOT NULL,                  -- 1-based, in file order
    data     TEXT NOT NULL,                     -- the row as a JSON object of text values
    PRIMARY KEY (doc_id, row_no)
);

-- As-arrived means as-arrived: nothing loaded may be changed or removed.
CREATE TRIGGER documents_no_update BEFORE UPDATE ON documents
BEGIN SELECT RAISE(ABORT, 'documents are stored as they arrived and cannot be changed'); END;
CREATE TRIGGER documents_no_delete BEFORE DELETE ON documents
BEGIN SELECT RAISE(ABORT, 'documents are stored as they arrived and cannot be deleted'); END;
CREATE TRIGGER source_rows_no_update BEFORE UPDATE ON source_rows
BEGIN SELECT RAISE(ABORT, 'source rows are stored as they arrived and cannot be changed'); END;
CREATE TRIGGER source_rows_no_delete BEFORE DELETE ON source_rows
BEGIN SELECT RAISE(ABORT, 'source rows are stored as they arrived and cannot be deleted'); END;

CREATE TABLE periods (
    period     TEXT PRIMARY KEY,                -- 'YYYY-MM'
    status     TEXT NOT NULL CHECK (status IN ('open', 'locked'))
);

-- Convenience views over the monthly files (all values stay text, exactly as received).
CREATE VIEW v_hr_census AS
SELECT d.doc_id, r.row_no, d.month,
       json_extract(r.data, '$.employee_id') AS employee_id, json_extract(r.data, '$.entity') AS entity,
       json_extract(r.data, '$.department') AS department, json_extract(r.data, '$.status') AS status,
       json_extract(r.data, '$.hire_date') AS hire_date, json_extract(r.data, '$.exit_date') AS exit_date,
       json_extract(r.data, '$.bank_account') AS bank_account, r.data
FROM documents d JOIN source_rows r USING (doc_id) WHERE d.kind = 'hr_census';

CREATE VIEW v_payroll_register AS
SELECT d.doc_id, r.row_no, d.month, d.entity,
       json_extract(r.data, '$.employee_id') AS employee_id, json_extract(r.data, '$.department') AS department,
       json_extract(r.data, '$.gross_pay') AS gross_pay, json_extract(r.data, '$.net_pay') AS net_pay, r.data
FROM documents d JOIN source_rows r USING (doc_id) WHERE d.kind = 'payroll_register';

CREATE VIEW v_payment_file AS
SELECT d.doc_id, r.row_no, d.month, d.entity,
       json_extract(r.data, '$.payment_id') AS payment_id, json_extract(r.data, '$.employee_id') AS employee_id,
       json_extract(r.data, '$.bank_account') AS bank_account, json_extract(r.data, '$.amount') AS amount,
       json_extract(r.data, '$.status') AS status, r.data
FROM documents d JOIN source_rows r USING (doc_id) WHERE d.kind = 'payment_file';

CREATE VIEW v_bank_statement AS
SELECT d.doc_id, r.row_no, d.month, d.entity,
       json_extract(r.data, '$.bank_line_id') AS bank_line_id, json_extract(r.data, '$.date') AS date,
       json_extract(r.data, '$.reference') AS reference, json_extract(r.data, '$.amount') AS amount, r.data
FROM documents d JOIN source_rows r USING (doc_id) WHERE d.kind = 'bank_statement';

CREATE VIEW v_equity_report AS
SELECT d.doc_id, r.row_no, d.month,
       json_extract(r.data, '$.grant_id') AS grant_id, json_extract(r.data, '$.employee_id') AS employee_id,
       json_extract(r.data, '$.entity') AS entity, json_extract(r.data, '$.status') AS status,
       json_extract(r.data, '$.expense_month_usd') AS expense_month_usd, r.data
FROM documents d JOIN source_rows r USING (doc_id) WHERE d.kind = 'equity_report';
