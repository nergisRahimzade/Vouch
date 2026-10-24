-- Layer 5: controls. Who may do what, what waits for whom, and an append-only record of it all.

CREATE TABLE IF NOT EXISTS clock (
    id   INTEGER PRIMARY KEY CHECK (id = 1),
    now  TEXT NOT NULL                          -- simulated time, ISO 8601
);

CREATE TABLE IF NOT EXISTS audit_log (
    seq        INTEGER PRIMARY KEY,
    at         TEXT NOT NULL,
    actor      TEXT NOT NULL,
    role       TEXT NOT NULL,
    action     TEXT NOT NULL,
    target     TEXT NOT NULL,
    outcome    TEXT NOT NULL,                   -- done | refused | requested | reminded | escalated | failed
    reason     TEXT NOT NULL,
    basis      TEXT NOT NULL,                   -- JSON: the data, rule, document or person behind it
    prev_hash  TEXT NOT NULL,
    hash       TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'the audit trail is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'the audit trail is append-only'); END;

CREATE TABLE IF NOT EXISTS cases (
    case_id       TEXT PRIMARY KEY,             -- the finding it came from
    close_period  TEXT NOT NULL,
    kind          TEXT NOT NULL,
    status        TEXT NOT NULL,                -- open | held | waiting | resolved
    summary       TEXT NOT NULL,
    opened_at     TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    note          TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS holds (
    hold_id      INTEGER PRIMARY KEY,
    case_id      TEXT NOT NULL,
    target_kind  TEXT NOT NULL,                 -- payment | grant | person | conversion | request
    target_ref   TEXT NOT NULL,
    reason       TEXT NOT NULL,
    placed_by    TEXT NOT NULL,
    placed_at    TEXT NOT NULL,
    released_by  TEXT,
    released_at  TEXT,
    release_note TEXT
);

CREATE TABLE IF NOT EXISTS approval_requests (
    request_id     TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,               -- entry | payment_release | funding | bank_change | accrual | grant
    subject_ref    TEXT NOT NULL,
    fingerprint    TEXT NOT NULL,               -- what exactly is asked; a rejected fingerprint is never resent
    payload        TEXT NOT NULL,
    amount         TEXT,
    currency       TEXT,
    case_id        TEXT,
    requested_by   TEXT NOT NULL,
    keyed_by       TEXT NOT NULL,
    needs          TEXT NOT NULL,               -- JSON list of roles, in order
    approvals      TEXT NOT NULL DEFAULT '[]',  -- JSON list of {actor, role, at, note}
    status         TEXT NOT NULL,               -- pending | approved | rejected | escalated
    assigned_role  TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    reminded_at    TEXT,
    escalated_at   TEXT,
    closed_at      TEXT,
    note           TEXT
);

CREATE TABLE IF NOT EXISTS payment_releases (
    payment_id   TEXT PRIMARY KEY,
    released_by  TEXT NOT NULL,
    released_at  TEXT NOT NULL,
    request_id   TEXT NOT NULL
);

-- Drafts become posted only through an approved request.
CREATE TRIGGER IF NOT EXISTS post_needs_approval BEFORE UPDATE OF status ON journal_entries
WHEN NEW.status = 'posted' AND OLD.status = 'draft' AND NOT EXISTS (
    SELECT 1 FROM approval_requests WHERE kind = 'entry' AND subject_ref = NEW.entry_id AND status = 'approved')
BEGIN SELECT RAISE(ABORT, 'an entry is posted only after its approval request is approved'); END;
CREATE TRIGGER IF NOT EXISTS posted_is_final BEFORE UPDATE ON journal_entries
WHEN OLD.status = 'posted'
BEGIN SELECT RAISE(ABORT, 'a posted entry cannot be changed; book a correction'); END;
