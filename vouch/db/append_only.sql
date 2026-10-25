-- Append-only books (idempotent: applied to new databases and to existing ones by controls.gates.ensure).
-- A draft that will never be posted (rejected by a reviewer, withdrawn by its drafter) is cancelled by
-- appending a void, never by deleting it.
CREATE TABLE IF NOT EXISTS entry_voids (
    entry_id   TEXT PRIMARY KEY REFERENCES journal_entries(entry_id),
    voided_by  TEXT NOT NULL,
    role       TEXT NOT NULL,
    reason     TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    basis      TEXT,                            -- JSON: the rejection or withdrawal behind it
    at         TEXT NOT NULL
);

-- The books are append-only. In an open month the only change is draft -> posted, with nothing else
-- altered; locked months keep their own stricter guard (periods.sql).
CREATE TRIGGER IF NOT EXISTS entries_append_only_delete BEFORE DELETE ON journal_entries
WHEN (SELECT status FROM periods WHERE period = OLD.period) IS NOT 'locked'
BEGIN SELECT RAISE(ABORT, 'the books are append-only: void a draft or book a correction'); END;
CREATE TRIGGER IF NOT EXISTS entries_append_only_update BEFORE UPDATE ON journal_entries
WHEN (SELECT status FROM periods WHERE period = OLD.period) IS NOT 'locked'
 AND (SELECT status FROM periods WHERE period = NEW.period) IS NOT 'locked'
 AND (OLD.status <> 'draft' OR NEW.status <> 'posted' OR NEW.entry_id IS NOT OLD.entry_id
      OR NEW.entity IS NOT OLD.entity OR NEW.period IS NOT OLD.period OR NEW.entry_date IS NOT OLD.entry_date
      OR NEW.type IS NOT OLD.type OR NEW.reason IS NOT OLD.reason OR NEW.currency IS NOT OLD.currency
      OR NEW.origin IS NOT OLD.origin OR NEW.created_by IS NOT OLD.created_by OR NEW.source_ref IS NOT OLD.source_ref
      OR NEW.corrects_period IS NOT OLD.corrects_period OR NEW.corrects IS NOT OLD.corrects
      OR NEW.created_at IS NOT OLD.created_at)
BEGIN SELECT RAISE(ABORT, 'the books are append-only: the only change is posting an approved draft'); END;
CREATE TRIGGER IF NOT EXISTS voided_never_posts BEFORE UPDATE OF status ON journal_entries
WHEN NEW.status = 'posted' AND EXISTS (SELECT 1 FROM entry_voids WHERE entry_id = NEW.entry_id)
BEGIN SELECT RAISE(ABORT, 'a voided draft can never be posted'); END;
CREATE TRIGGER IF NOT EXISTS lines_append_only_update BEFORE UPDATE ON journal_lines
WHEN (SELECT p.status FROM periods p JOIN journal_entries e ON e.period = p.period WHERE e.entry_id = OLD.entry_id) IS NOT 'locked'
BEGIN SELECT RAISE(ABORT, 'the books are append-only: journal lines never change'); END;
CREATE TRIGGER IF NOT EXISTS lines_append_only_delete BEFORE DELETE ON journal_lines
WHEN (SELECT p.status FROM periods p JOIN journal_entries e ON e.period = p.period WHERE e.entry_id = OLD.entry_id) IS NOT 'locked'
BEGIN SELECT RAISE(ABORT, 'the books are append-only: journal lines never change'); END;
CREATE TRIGGER IF NOT EXISTS sources_append_only_update BEFORE UPDATE ON line_sources
BEGIN SELECT RAISE(ABORT, 'the books are append-only: line sources never change'); END;
CREATE TRIGGER IF NOT EXISTS sources_append_only_delete BEFORE DELETE ON line_sources
WHEN (SELECT p.status FROM periods p JOIN journal_entries e ON e.period = p.period WHERE e.entry_id = OLD.entry_id) IS NOT 'locked'
BEGIN SELECT RAISE(ABORT, 'the books are append-only: line sources never change'); END;
CREATE TRIGGER IF NOT EXISTS voids_append_only_update BEFORE UPDATE ON entry_voids
BEGIN SELECT RAISE(ABORT, 'voids are append-only'); END;
CREATE TRIGGER IF NOT EXISTS voids_append_only_delete BEFORE DELETE ON entry_voids
BEGIN SELECT RAISE(ABORT, 'voids are append-only'); END;
CREATE TRIGGER IF NOT EXISTS voids_only_drafts BEFORE INSERT ON entry_voids
WHEN (SELECT status FROM journal_entries WHERE entry_id = NEW.entry_id) IS NOT 'draft'
BEGIN SELECT RAISE(ABORT, 'only a draft can be voided; a posted entry is corrected by a new entry'); END;

-- Payroll exports and recipient notices are append-only: payroll moves a draft's status, nothing else changes.
CREATE TRIGGER IF NOT EXISTS payroll_exports_no_delete BEFORE DELETE ON payroll_exports
BEGIN SELECT RAISE(ABORT, 'payroll exports are append-only; draft a delta instead'); END;
CREATE TRIGGER IF NOT EXISTS payroll_exports_only_status BEFORE UPDATE ON payroll_exports
WHEN NEW.id IS NOT OLD.id OR NEW.period IS NOT OLD.period OR NEW.employee IS NOT OLD.employee
  OR NEW.event IS NOT OLD.event OR NEW.taxable_amount IS NOT OLD.taxable_amount OR NEW.method IS NOT OLD.method
  OR NEW.origin IS NOT OLD.origin OR NEW.corrects IS NOT OLD.corrects
BEGIN SELECT RAISE(ABORT, 'payroll exports are append-only; draft a delta instead'); END;
CREATE TRIGGER IF NOT EXISTS recipient_notices_no_delete BEFORE DELETE ON recipient_notices
BEGIN SELECT RAISE(ABORT, 'recipient notices are append-only'); END;
