CREATE TABLE IF NOT EXISTS agent_runs (
 run_id TEXT PRIMARY KEY, mode TEXT NOT NULL, model TEXT NOT NULL, started_at TEXT NOT NULL,
 status TEXT NOT NULL, result TEXT, metrics TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS decisions (case_id TEXT PRIMARY KEY, decision TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS info_requests (
 request_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, recipient TEXT NOT NULL, about TEXT NOT NULL,
 status TEXT NOT NULL, created_at TEXT NOT NULL, reminded_at TEXT, escalated_at TEXT,
 UNIQUE(case_id, recipient, about));
CREATE TABLE IF NOT EXISTS received_facts (
 request_id TEXT PRIMARY KEY REFERENCES info_requests(request_id), sender TEXT NOT NULL, data TEXT NOT NULL,
 received_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learned_corrections (
 id TEXT PRIMARY KEY, case_id TEXT NOT NULL, decided_by TEXT NOT NULL, method TEXT NOT NULL,
 reason TEXT NOT NULL, source TEXT NOT NULL, at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS facts_immutable BEFORE UPDATE ON received_facts
BEGIN SELECT RAISE(ABORT, 'received facts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS facts_no_delete BEFORE DELETE ON received_facts
BEGIN SELECT RAISE(ABORT, 'received facts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS memory_immutable BEFORE UPDATE ON learned_corrections
BEGIN SELECT RAISE(ABORT, 'learned corrections are immutable'); END;

CREATE TABLE IF NOT EXISTS followup_facts (
 id INTEGER PRIMARY KEY, request_id TEXT NOT NULL REFERENCES info_requests(request_id),
 sender TEXT NOT NULL, data TEXT NOT NULL, received_at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS followups_immutable BEFORE UPDATE ON followup_facts
BEGIN SELECT RAISE(ABORT, 'follow-up facts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS followups_no_delete BEFORE DELETE ON followup_facts
BEGIN SELECT RAISE(ABORT, 'follow-up facts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS memory_no_delete BEFORE DELETE ON learned_corrections
BEGIN SELECT RAISE(ABORT, 'learned corrections are immutable'); END;
