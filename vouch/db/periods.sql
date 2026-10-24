-- Period close. A locked month accepts no ledger writes; only the controller changes status.

CREATE TABLE period_events (
    id      INTEGER PRIMARY KEY,
    period  TEXT NOT NULL REFERENCES periods(period),
    action  TEXT NOT NULL CHECK (action IN ('lock', 'reopen')),
    actor   TEXT NOT NULL,
    role    TEXT NOT NULL,
    reason  TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    at      TEXT NOT NULL
);

-- A status change must follow a logged controller event for that period and action.
CREATE TRIGGER periods_status_needs_controller BEFORE UPDATE OF status ON periods
WHEN NEW.status <> OLD.status AND NOT EXISTS (
    SELECT 1 FROM period_events ev
    WHERE ev.id = (SELECT MAX(id) FROM period_events WHERE period = NEW.period)
      AND ev.role = 'controller'
      AND ev.action = CASE NEW.status WHEN 'locked' THEN 'lock' ELSE 'reopen' END)
BEGIN SELECT RAISE(ABORT, 'only the controller can lock or reopen a period, and it must be logged'); END;

CREATE TRIGGER entries_locked_insert BEFORE INSERT ON journal_entries
WHEN (SELECT status FROM periods WHERE period = NEW.period) = 'locked'
BEGIN SELECT RAISE(ABORT, 'period is locked: book the correction in the open month'); END;
CREATE TRIGGER entries_locked_update BEFORE UPDATE ON journal_entries
WHEN (SELECT status FROM periods WHERE period = OLD.period) = 'locked'
  OR (SELECT status FROM periods WHERE period = NEW.period) = 'locked'
BEGIN SELECT RAISE(ABORT, 'period is locked: book the correction in the open month'); END;
CREATE TRIGGER entries_locked_delete BEFORE DELETE ON journal_entries
WHEN (SELECT status FROM periods WHERE period = OLD.period) = 'locked'
BEGIN SELECT RAISE(ABORT, 'period is locked: book the correction in the open month'); END;

CREATE TRIGGER lines_locked_insert BEFORE INSERT ON journal_lines
WHEN (SELECT p.status FROM periods p JOIN journal_entries e ON e.period = p.period WHERE e.entry_id = NEW.entry_id) = 'locked'
BEGIN SELECT RAISE(ABORT, 'period is locked: book the correction in the open month'); END;
CREATE TRIGGER lines_locked_update BEFORE UPDATE ON journal_lines
WHEN (SELECT p.status FROM periods p JOIN journal_entries e ON e.period = p.period WHERE e.entry_id = OLD.entry_id) = 'locked'
BEGIN SELECT RAISE(ABORT, 'period is locked: book the correction in the open month'); END;
CREATE TRIGGER lines_locked_delete BEFORE DELETE ON journal_lines
WHEN (SELECT p.status FROM periods p JOIN journal_entries e ON e.period = p.period WHERE e.entry_id = OLD.entry_id) = 'locked'
BEGIN SELECT RAISE(ABORT, 'period is locked: book the correction in the open month'); END;

CREATE TRIGGER sources_locked_insert BEFORE INSERT ON line_sources
WHEN (SELECT p.status FROM periods p JOIN journal_entries e ON e.period = p.period WHERE e.entry_id = NEW.entry_id) = 'locked'
BEGIN SELECT RAISE(ABORT, 'period is locked: book the correction in the open month'); END;
CREATE TRIGGER sources_locked_change BEFORE DELETE ON line_sources
WHEN (SELECT p.status FROM periods p JOIN journal_entries e ON e.period = p.period WHERE e.entry_id = OLD.entry_id) = 'locked'
BEGIN SELECT RAISE(ABORT, 'period is locked: book the correction in the open month'); END;
