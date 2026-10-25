"""Entries the agent drafts. Code computes every amount from the sources; the model only decides
whether an entry is needed. Each template returns a draft Entry with its evidence rows."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from vouch import dates
from vouch.calc import fx, stock
from vouch.calc.pay import calculate_pay, YTD_KEYS
from vouch.calc.rounding import DEPARTMENT_ORDER, translate_split
from vouch.db.ledger import Entry, Line, SourceRef
from vouch.db.subledger import REGISTER_COLUMNS
from vouch.money import ZERO, cents
from vouch.recon.sources import Row, Sources

LIABILITY = {"income_tax": "2200", "employee_social": "2210", "employer_social": "2210", "employer_pension": "2220"}
EXPENSE = {"employer_social": "6100", "employer_pension": "6200"}


class CannotDraft(ValueError):
    """The case has no entry to draft, or the sources do not support one."""


def _ref(row: Row) -> SourceRef:
    return SourceRef(row.doc_id, row.row_no)


def _components(row: Row) -> dict[str, Decimal]:
    return {kind: sum((Decimal(row.data[c]) for c in cols if row.data.get(c)), ZERO)
            for kind, cols in REGISTER_COLUMNS.items()}


def _lines(debits: dict, credits: dict, refs: dict, memo: str, fx_rate=None, fx_basis=None) -> list[Line]:
    """debits/credits: {(account, department): amount}; refs: {(account, department): [SourceRef]}.
    A negative amount moves to the other side."""
    out = []
    for side, items in (("debit", debits), ("credit", credits)):
        for (account, department), value in sorted(items.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
            if not value:
                continue
            debit = value if (side == "debit") == (value > 0) else ZERO
            amount = abs(value)
            out.append(Line(account, debit=amount if debit else ZERO, credit=ZERO if debit else amount,
                            department=department, memo=memo, fx_rate=fx_rate, fx_basis=fx_basis,
                            sources=refs.get((account, department), [])))
    return out


def _entry(entry_id, entity, period, reason, lines, corrects=None) -> Entry:
    return Entry(entry_id, entity, period, dates.month_end(period).isoformat(), reason, lines, "Vouch",
                 origin="vouch", status="draft", corrects_period=corrects)


def find_row(src: Sources, path: str, row_no: int) -> Row:
    for rows in src.by_kind.values():
        for row in rows:
            if row.path == path and row.row_no == row_no:
                return row
    raise CannotDraft(f"source row {path}#{row_no} not found")


# -- the open month's own entries ------------------------------------------------
def payroll_accrual(src: Sources, entity: str, exclude: set[str]) -> Entry:
    """The open month's payroll for one entity, from its register, leaving out held people."""
    month = src.open_month
    debits, credits, refs = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO), defaultdict(list)
    for row in src.rows("payroll_register", month, entity):
        if row["employee_id"] in exclude:
            continue
        department, parts = row["department"], _components(row)
        for key, value in ((("6000", department), Decimal(row["gross_pay"])),
                           (("6100", department), parts["employer_social"]),
                           (("6200", department), parts["employer_pension"])):
            debits[key] += value
            refs[key].append(_ref(row))
        for key, value in ((("2100", None), Decimal(row["net_pay"])),
                           (("2200", None), parts["income_tax"]),
                           (("2210", None), parts["employee_social"] + parts["employer_social"]),
                           (("2220", None), parts["employer_pension"])):
            credits[key] += value
            refs[key].append(_ref(row))
    if not debits:
        raise CannotDraft(f"no payroll to accrue for {entity}")
    return _entry(f"VCH-{month}-{entity}-PAYROLL", entity, month, f"Payroll {month} {entity}",
                  _lines(debits, credits, refs, f"Payroll {month} {entity}"))


def grant_expense(src: Sources, month: str) -> dict[str, tuple[Decimal, Row, str, str]]:
    """grant -> (USD expense this month, platform row, entity, department), from the signed terms and HR dates."""
    signed = {r["grant_id"]: r for r in src.rows("signed_grants")}
    approved = {r["grant_id"] for r in src.rows("board_approvals")}
    hr = {r["employee_id"]: r for r in src.rows("hr_census", month)}
    out = {}
    for row in src.rows("equity_report", month):
        grant = row["grant_id"]
        if grant not in approved or grant not in signed or row["employee_id"] not in hr:
            continue
        s, person = signed[grant], hr[row["employee_id"]]
        terms = stock.GrantTerms(int(s["shares"]), Decimal(s["fair_value_per_share_usd"]),
                                 dates.parse_date(s["grant_date"]), dates.parse_date(s["vest_start"]),
                                 int(s["vest_months"]), int(s["cliff_months"]))
        expense = stock.month_expense(terms, dates.parse_date(person["exit_date"]) if person["exit_date"] else None,
                                      month).expense
        out[grant] = (expense, row, person["entity"], person["department"])
    return out


def stock_expense(src: Sources, entity: str, exclude_grants: set[str]) -> Entry:
    """The open month's stock compensation for one entity: expense from signed terms and HR dates,
    translated once at the month's average rate, split by department (leftover cent on a fixed line)."""
    month = src.open_month
    usd, refs = defaultdict(lambda: ZERO), defaultdict(list)
    for grant, (expense, row, grant_entity, department) in grant_expense(src, month).items():
        if grant_entity != entity or grant in exclude_grants or not expense:
            continue
        usd[department] += expense
        refs[("6300", department)].append(_ref(row))
    if not usd or not any(usd.values()):
        raise CannotDraft(f"no stock expense for {entity}")
    local, translation = translate_split(dict(usd), src.currency(entity), month, DEPARTMENT_ORDER)
    debits = {("6300", d): v for d, v in local.items()}
    credits = {("3100", None): translation.amount_local}
    refs[("3100", None)] = [r for rs in refs.values() for r in rs]
    rate = str(translation.rate.rate)
    return _entry(f"VCH-{month}-{entity}-STOCKCOMP", entity, month, f"Stock compensation {month} {entity} (ASC 718)",
                  _lines(debits, credits, refs, f"Stock compensation {month} {entity}", rate, translation.basis))


# -- corrections for cases ------------------------------------------------------
def why_no_correction(src: Sources, case: dict) -> str | None:
    """Why no correcting entry can be drafted for this case, or None when one can."""
    kind = case["kind"]
    if kind not in CORRECTIONS:
        return f"a {kind} case is settled by a hold, a question or a request, not an entry"
    if kind == "paid_after_exit" and not src.is_closed(case["period"]):
        return "the payment has not left yet; hold it instead of drafting a recovery"
    return None


def correction(src: Sources, case: dict) -> Entry:
    reason = why_no_correction(src, case)
    if reason:
        raise CannotDraft(reason)
    return CORRECTIONS[case["kind"]](src, case)


def _evidence_rows(src: Sources, case: dict, kind: str) -> list[Row]:
    return [find_row(src, e["path"], n) for e in case["evidence"] if e["source"] == kind for n in e["rows"]]


def _recovery(src: Sources, case: dict) -> Entry:
    row = _evidence_rows(src, case, "payroll_register")[0]
    gross, overpaid = Decimal(row["gross_pay"]), Decimal(case["amount"])
    ratio, parts = overpaid / gross, _components(row)
    share = {k: cents(v * ratio) for k, v in parts.items()}
    net = overpaid - share["income_tax"] - share["employee_social"]
    department, ref = row["department"], [_ref(row)]
    debits = {("2200", None): share["income_tax"], ("2210", None): share["employee_social"] + share["employer_social"],
              ("2220", None): share["employer_pension"], ("9999", None): net}
    credits = {("6000", department): overpaid, ("6100", department): share["employer_social"],
               ("6200", department): share["employer_pension"]}
    refs = {k: ref for k in list(debits) + list(credits)}
    memo = f"Recover pay after exit ({case['period']}); net {net} due back from the leaver (held in suspense)"
    return _entry(f"VCH-{case['finding_id']}", row.entity, src.open_month, memo, _lines(debits, credits, refs, memo),
                  corrects=case["period"])


def _pension_shortfall(src: Sources, case: dict) -> Entry:
    entity, month = case["entity"], case["period"]
    rate = src.pension_rate(entity)
    debits, refs = defaultdict(lambda: ZERO), defaultdict(list)
    for row in src.rows("payroll_register", month, entity):
        keys = YTD_KEYS[entity]
        base = {k: Decimal(row["gross_pay"]) if k == "gross" else Decimal(row[f"period_{k}"]) for k in keys}
        before = {k: Decimal(row[f"ytd_{k}"]) - base[k] for k in keys}
        due = calculate_pay(entity, int(row["tax_year"]), int(row["tax_period"]), Decimal(row["gross_pay"]), before,
                            employer_pension_rate=rate,
                            days=(int(row["days_paid"]), int(row["days_in_month"]))).employer["employer_pension"]
        gap = due - Decimal(row["employer_pension"])
        if gap:
            debits[("6200", row["department"])] += gap
            refs[("6200", row["department"])].append(_ref(row))
    total = sum(debits.values(), ZERO)
    if total != Decimal(case["amount"]):
        raise CannotDraft(f"recomputed shortfall {total} differs from the finding's {case['amount']}")
    refs[("2220", None)] = [r for rs in refs.values() for r in rs]
    memo = f"Employer pension at the policy rate for {month}"
    return _entry(f"VCH-{case['finding_id']}", entity, src.open_month, memo,
                  _lines(dict(debits), {("2220", None): total}, refs, memo), corrects=month)


def _forfeiture_reversal(src: Sources, case: dict) -> Entry:
    grant = case["subject"]["grant_id"]
    rows = [r for r in src.rows("equity_report") if r["grant_id"] == grant]
    hr = next(r for r in reversed(src.rows("hr_census")) if r["employee_id"] == case["subject"]["employee_id"])
    entity, department = hr["entity"], hr["department"]
    translation = fx.translate(Decimal(case["amount"]), src.currency(entity), src.open_month)
    refs = {("3100", None): [_ref(r) for r in rows], ("6300", department): [_ref(r) for r in rows]}
    memo = f"Reverse expense on a forfeited grant ({grant})"
    return _entry(f"VCH-{case['finding_id']}", entity, src.open_month, memo,
                  _lines({("3100", None): translation.amount_local}, {("6300", department): translation.amount_local},
                         refs, memo, str(translation.rate.rate), translation.basis), corrects=case["period"])


def _arrears(src: Sources, case: dict) -> Entry:
    rows = _evidence_rows(src, case, "payroll_register")
    change = _evidence_rows(src, case, "salary_changes")
    department, entity = rows[0]["department"], rows[0].entity
    refs = {("6000", department): [_ref(r) for r in rows + change], ("2100", None): [_ref(r) for r in rows + change]}
    memo = f"Arrears for a backdated salary change ({', '.join(case['subject']['closed_months'])})"
    amount = Decimal(case["amount"])
    return _entry(f"VCH-{case['finding_id']}", entity, src.open_month, memo,
                  _lines({("6000", department): amount}, {("2100", None): amount}, refs, memo), corrects=case["period"])


CORRECTIONS = {"paid_after_exit": _recovery, "contribution_rate_differs_from_policy": _pension_shortfall,
               "forfeiture_not_recorded": _forfeiture_reversal, "backdated_salary_change": _arrears}
