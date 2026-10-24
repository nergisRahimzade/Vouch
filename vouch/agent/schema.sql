-- Layer 6: the agent's own records. Every action it takes also goes to the audit trail.

CREATE TABLE IF NOT EXISTS questions (
    question_id   TEXT PRIMARY KEY,
    case_id       TEXT NOT NULL,
    to_role       TEXT NOT NULL,
    to_person     TEXT NOT NULL,
    fact_needed   TEXT NOT NULL,
    sources       TEXT NOT NULL,               -- JSON: the sources that disagree
    if_yes        TEXT NOT NULL,               -- what happens on each answer (one of the allowed next steps)
    if_no         TEXT NOT NULL,
    if_partial    TEXT NOT NULL,
    status        TEXT NOT NULL,               -- open | answered
    answer        TEXT,                        -- yes | no | partial
    answered_by   TEXT,
    note          TEXT,
    asked_at      TEXT NOT NULL,
    answered_at   TEXT,
    reminded_at   TEXT,
    escalated_at  TEXT,
    follows       TEXT                         -- the question this one follows up
);

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id         TEXT PRIMARY KEY,
    close_period   TEXT NOT NULL,
    mode           TEXT NOT NULL,              -- model | scripted
    model          TEXT,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    status         TEXT NOT NULL,              -- running | done | failed
    input_tokens   INTEGER NOT NULL DEFAULT 0,
    output_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd       TEXT NOT NULL DEFAULT '0.00',
    report         TEXT,
    report_status  TEXT,                       -- accepted | rejected
    report_problems TEXT
);
