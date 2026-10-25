-- Double-entry general ledger, one functional currency per entity.

CREATE TABLE journal_entries (
    entry_id         TEXT PRIMARY KEY,
    entity           TEXT NOT NULL,
    period           TEXT NOT NULL REFERENCES periods(period),
    entry_date       TEXT NOT NULL,
    type             TEXT NOT NULL,             -- ap_deposit | ap_invoice | payment | prepaid_release | accrual
                                                -- | accrual_reversal | reclass | discount | history
    reason           TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    currency         TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('draft', 'posted')),
    origin           TEXT NOT NULL,             -- history (as loaded) | vouch
    created_by       TEXT NOT NULL,
    source_ref       TEXT,                      -- the invoice (or task) the entry books
    corrects_period  TEXT,                      -- set when the entry books something that belongs to a closed month
    reverses         TEXT,                      -- the accrual an automatic reversal mirrors
    created_at       TEXT NOT NULL
);

CREATE TABLE journal_lines (
    entry_id     TEXT NOT NULL REFERENCES journal_entries(entry_id),
    line_no      INTEGER NOT NULL,
    account      TEXT NOT NULL REFERENCES accounts(code),
    cost_center  TEXT,
    event        TEXT,
    debit        TEXT NOT NULL,                 -- exact decimal text, two places
    credit       TEXT NOT NULL,
    fx_rate      TEXT,
    fx_basis     TEXT,                          -- which rate and period fx_rate is (required with fx_rate)
    memo         TEXT,
    PRIMARY KEY (entry_id, line_no)
);

-- What each line rests on: rows of the system of record (an invoice line, a clause, an evidence row, ...).
CREATE TABLE line_sources (
    entry_id   TEXT NOT NULL,
    line_no    INTEGER NOT NULL,
    ref_table  TEXT NOT NULL,
    ref_id     TEXT NOT NULL,
    FOREIGN KEY (entry_id, line_no) REFERENCES journal_lines(entry_id, line_no)
);
CREATE INDEX line_sources_by_line ON line_sources (entry_id, line_no);

-- History entries the ledger could not accept, with every reason. Never dropped silently.
CREATE TABLE refused_entries (
    entry_id  TEXT NOT NULL,
    period    TEXT,
    reasons   TEXT NOT NULL,                    -- JSON list
    source    TEXT NOT NULL
);
