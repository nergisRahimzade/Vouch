-- Double-entry general ledger, one functional currency per entity.

CREATE TABLE journal_entries (
    entry_id         TEXT PRIMARY KEY,
    entity           TEXT NOT NULL,
    period           TEXT NOT NULL REFERENCES periods(period),
    entry_date       TEXT NOT NULL,
    reason           TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    currency         TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('draft', 'posted')),
    origin           TEXT NOT NULL,             -- booked_ledger (history as received) | vouch
    created_by       TEXT NOT NULL,
    corrects_period  TEXT,                      -- set when the entry corrects a closed month
    created_at       TEXT NOT NULL
);

CREATE TABLE journal_lines (
    entry_id    TEXT NOT NULL REFERENCES journal_entries(entry_id),
    line_no     INTEGER NOT NULL,
    account     TEXT NOT NULL REFERENCES accounts(code),
    department  TEXT,
    debit       TEXT NOT NULL,                  -- exact decimal text, two places
    credit      TEXT NOT NULL,
    fx_rate     TEXT,
    memo        TEXT,
    PRIMARY KEY (entry_id, line_no)
);

-- What each line rests on: evidence rows (or whole documents) and, for history, the row it was booked as.
CREATE TABLE line_sources (
    entry_id  TEXT NOT NULL,
    line_no   INTEGER NOT NULL,
    role      TEXT NOT NULL CHECK (role IN ('evidence', 'booked_as')),
    doc_id    INTEGER NOT NULL REFERENCES documents(doc_id),
    row_no    INTEGER,                          -- NULL: the whole document
    FOREIGN KEY (entry_id, line_no) REFERENCES journal_lines(entry_id, line_no)
);
CREATE INDEX line_sources_by_line ON line_sources (entry_id, line_no);

-- Booked-ledger entries the ledger could not accept, with every reason. Never dropped silently.
CREATE TABLE refused_entries (
    entry_id  TEXT NOT NULL,
    period    TEXT,
    reasons   TEXT NOT NULL,                    -- JSON list
    source    TEXT NOT NULL
);
