"""Pairwise checks. Each compares two or more sources for the same fact across every loaded
month, trusts the system of record for that fact, and turns each real difference into a
finding. Checks look for kinds of error; none names a person, grant, payment or month."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from vouch import dates
from vouch.calc import fx, stock
from vouch.calc.pay import EMPLOYEE_COMPONENTS, EMPLOYER_COMPONENTS, YTD_KEYS, calculate_pay
from vouch.money import ZERO, cents, fmt
from vouch.recon.findings import Evidence, Finding, Findings
from vouch.recon.sources import Row, Sources

LIVE_GRANT = {"active", "fully_vested"}
ENDED_GRANT = {"forfeited", "vested_and_left", "cancelled"}


def _d(text: str | None) -> date | None:
    return dates.parse_date(text) if text else None


def _days(first: date, last: date) -> int:
    return max(0, (last - first).days + 1)


def _action_for_money(src: Sources, month: str, open_action: str, closed_action: str) -> str:
    return closed_action if src.is_closed(month) else open_action


# -- HR census helpers ---------------------------------------------------------
def hr_by_month(src: Sources, out: Findings) -> dict[str, dict[str, Row]]:
    """HR rows by month and employee. An exact duplicate row is explained; differing duplicates are a case."""
    result: dict[str, dict[str, Row]] = {}
    for month in src.months:
        seen: dict[str, Row] = {}
        for row in src.rows("hr_census", month):
            emp = row["employee_id"]
            if emp not in seen:
                seen[emp] = row
                continue
            first = seen[emp]
            same = first.data == row.data
            out.add(Finding(
                "hr_census_integrity", "duplicate_source_row" if same else "conflicting_hr_rows", month, row["entity"],
                "employment", {"employee_id": emp},
                "HR export lists the same person twice with identical details" if same
                else "HR export lists the same person twice with different details",
                [Evidence.of(first, "first row"), Evidence.of(row, "repeated row")], ["FIN-09"], ZERO,
                src.currency(row["entity"]), "no money involved", "deduplicate" if same else "ask_hr", None if same else "hr",
                None if same else src.person_with_role("hr"), disposition="explained" if same else "case",
                explanation="An exact repeat of a row is one person; the duplicate is ignored." if same else "",
                merge_key=emp))
        result[month] = seen
    return result


def entity_window(hr: Row, month: str, entity: str, previous: Row | None) -> tuple[date, date] | None:
    """Days in the month HR says the person worked in `entity`, or None if HR puts them elsewhere."""
    start, end = dates.month_start(month), dates.month_end(month)
    first = max(start, _d(hr["hire_date"]))
    last = min(end, _d(hr["exit_date"]) or end)
    since = _d(hr["entity_since"])
    if hr["entity"] == entity:
        first = max(first, since)
    else:
        # Only the month of a move may still pay the old entity, up to the day before the move.
        if not (start < since <= end) or (previous is not None and previous["entity"] != entity):
            return None
        last = min(last, since - timedelta(days=1))
    return (first, last) if first <= last else (first, first - timedelta(days=1))


# -- HR vs payroll register ----------------------------------------------------
def hr_vs_register(src: Sources, hr: dict[str, dict[str, Row]], out: Findings, flagged: set) -> None:
    for i, month in enumerate(src.months):
        previous_hr = hr.get(src.months[i - 1], {}) if i else {}
        paid_in = defaultdict(set)
        for row in src.rows("payroll_register", month):
            paid_in[row["employee_id"]].add(row.entity)
        for row in src.rows("payroll_register", month):
            emp, entity, currency = row["employee_id"], row.entity, row["currency"]
            h = hr[month].get(emp)
            gross, net = row.amount("gross_pay"), row.amount("net_pay")
            if h is None:
                flagged.add((month, emp))
                out.add(Finding(
                    "hr_vs_register", "paid_not_in_hr", month, entity, "employment",
                    {"employee_id": emp, "entity": entity},
                    "The register pays someone HR has no record of",
                    [Evidence.of(row, f"pays {emp} {fmt(net)} net"),
                     Evidence("hr_census", hr_path(src, month), (), f"no row for {emp}")],
                    ["FIN-09", "PAY-03"], net, currency, "net pay for the month",
                    _action_for_money(src, month, "hold_payment_send_nothing", "draft_recovery_in_open_month"),
                    "hr", src.person_with_role("hr"), merge_key=emp))
                continue
            dim = dates.days_in_month(month)
            exit_date = _d(h["exit_date"])
            window = entity_window(h, month, entity, previous_hr.get(emp))
            if exit_date and exit_date < dates.month_start(month):
                flagged.add((month, emp))
                out.add(_paid_after_exit(src, month, row, h, gross, gross, net, "the whole month"))
            elif window is None:
                flagged.add((month, emp))
                out.add(_entity_mismatch(src, month, emp, h, Evidence.of(row, f"paid by {entity}"), net, currency))
            else:
                allowed = _days(*window)
                expected = cents(row.amount("annual_salary") / 12 * allowed / dim) if allowed < dim else \
                    cents(row.amount("annual_salary") / 12)
                if int(row["days_paid"]) > allowed and exit_date and exit_date <= dates.month_end(month):
                    flagged.add((month, emp))
                    out.add(_paid_after_exit(src, month, row, h, gross - expected, gross, net,
                                             f"{int(row['days_paid']) - allowed} days after the exit"))
                elif int(row["days_paid"]) != allowed:
                    out.add(Finding(
                        "hr_vs_register", "days_paid_differ_from_hr", month, entity, "employment",
                        {"employee_id": emp, "entity": entity},
                        f"The register pays {row['days_paid']} days; HR dates give {allowed}",
                        [Evidence.of(row, f"{row['days_paid']} days paid"),
                         Evidence.of(h, f"hired {h['hire_date']}, exit {h['exit_date'] or 'none'}, "
                                        f"in {h['entity']} since {h['entity_since']}")],
                        ["PAY-02", "FIN-09"], gross - expected, currency, "gross paid minus gross for HR's days",
                        _action_for_money(src, month, "hold_and_correct", "draft_correction_in_open_month"),
                        "hr", src.person_with_role("hr"), merge_key=emp))
        # Employed per HR in this month but paid by no entity at all.
        for emp, h in hr[month].items():
            start, end = dates.month_start(month), dates.month_end(month)
            employed = _d(h["hire_date"]) <= end and (not h["exit_date"] or _d(h["exit_date"]) >= start)
            if employed and not paid_in.get(emp):
                out.add(Finding(
                    "hr_vs_register", "employee_not_paid", month, h["entity"], "employment",
                    {"employee_id": emp, "entity": h["entity"]},
                    "HR shows the person employed this month but no register pays them",
                    [Evidence.of(h, f"employed in {h['entity']}"),
                     Evidence("payroll_register", f"months/{month}/payroll_register_*.csv", (), f"no row for {emp}")],
                    ["FIN-09", "PAY-01"], ZERO, src.currency(h["entity"]), "pay owed is not yet computed",
                    "ask_hr_and_payroll", "hr", src.person_with_role("hr"), merge_key=emp))


def hr_path(src: Sources, month: str) -> str:
    rows = src.rows("hr_census", month)
    return rows[0].path if rows else f"months/{month}/hr_census.csv"


def _paid_after_exit(src, month, row, h, overpaid, gross, net, how) -> Finding:
    """Closed month: the money left, so the amount is the gross to recover. Open month: the payment
    has not been released, so the amount is the net payment held."""
    closed = src.is_closed(month)
    return Finding(
        "hr_vs_register", "paid_after_exit", month, row.entity, "employment",
        {"employee_id": row["employee_id"], "entity": row.entity, "exit_date": h["exit_date"]},
        f"The register pays a leaver for {how} (HR exit date {h['exit_date']}): gross {fmt(overpaid)} "
        f"after the exit, net payment {fmt(net)}",
        [Evidence.of(row, f"pays {row['days_paid']} days, gross {fmt(gross)}, net {fmt(net)}"),
         Evidence.of(h, f"left on {h['exit_date']}")],
        ["PAY-05", "FIN-09"] + (["FIN-05"] if closed else []), overpaid if closed else net, row["currency"],
        "gross paid for days after the exit date, to recover" if closed else "net payment held",
        _action_for_money(src, month, "hold_payment_and_recover", "draft_recovery_in_open_month"),
        "hr", src.person_with_role("hr"), merge_key=row["employee_id"])


def _entity_mismatch(src, month, emp, h, evidence, amount, currency) -> Finding:
    return Finding(
        "hr_vs_register", "entity_differs_from_hr", month, h["entity"], "employment",
        {"employee_id": emp, "hr_entity": h["entity"], "department": h["department"]},
        f"HR has the person in {h['entity']} since {h['entity_since']}; another source still uses a different entity",
        [evidence, Evidence.of(h, f"in {h['entity']} since {h['entity_since']}")],
        ["PAY-07", "FIN-09"], amount, currency, "net pay held until the transfer is confirmed",
        "hold_and_ask_department_head", "department_head",
        src.person_with_role("department_head", h["department"]), merge_key=emp)


# -- Approved salary vs paid salary --------------------------------------------
def salary_vs_register(src: Sources, out: Findings, flagged: set) -> None:
    changes = defaultdict(list)
    for row in src.rows("salary_changes"):
        changes[row["employee_id"]].append(row)
    backdated: dict[str, dict] = {}
    for month in src.months:
        end = dates.month_end(month)
        for row in src.rows("payroll_register", month):
            emp = row["employee_id"]
            if (month, emp) in flagged:
                continue
            candidates = sorted((c for c in changes.get(emp, []) if c["currency"] == row["currency"]
                                 and _d(c["effective_date"]) <= end), key=lambda c: (c["effective_date"], c["change_id"]))
            if not candidates:
                out.add(Finding(
                    "salary_vs_register", "salary_without_approval", month, row.entity, "salary",
                    {"employee_id": emp, "entity": row.entity}, "The register pays a salary no approved change sets",
                    [Evidence.of(row, f"annual salary {row['annual_salary']}"),
                     Evidence("salary_changes", "standing/salary_changes.csv", (), "no approved salary")],
                    ["PAY-03", "FIN-09"], row.amount("gross_pay"), row["currency"], "gross paid without approval",
                    "hold_payment", "hr", src.person_with_role("hr"), merge_key=emp))
                continue
            approved = candidates[-1]
            paid, expected = row.amount("annual_salary"), Decimal(approved["annual_salary"])
            if paid == expected:
                continue
            dim, days = dates.days_in_month(month), int(row["days_paid"])
            should = cents(expected / 12) if days >= dim else cents(expected / 12 * days / dim)
            difference = should - row.amount("gross_pay")
            if _d(approved["approved_on"]) > end and src.is_closed(month):
                item = backdated.setdefault(approved["change_id"], {"change": approved, "rows": [], "arrears": ZERO})
                item["rows"].append(row)
                item["arrears"] += difference
            else:
                out.add(Finding(
                    "salary_vs_register", "salary_differs_from_approval", month, row.entity, "salary",
                    {"employee_id": emp, "entity": row.entity, "change_id": approved["change_id"]},
                    f"The register pays {fmt(paid)} a year; the approved salary is {fmt(expected)}",
                    [Evidence.of(row, f"annual salary {fmt(paid)}"),
                     Evidence(approved.kind, approved.path, (approved.row_no,), f"approved {fmt(expected)} "
                              f"from {approved['effective_date']}")],
                    ["PAY-03", "FIN-09"], difference, row["currency"], "gross for the month at the approved salary "
                    "minus gross paid", "hold_and_correct", "hr", src.person_with_role("hr"), merge_key=emp))
    for change_id, item in backdated.items():
        change, rows = item["change"], item["rows"]
        months = [r.month for r in rows]
        out.add(Finding(
            "salary_vs_register", "backdated_salary_change", months[0], rows[0].entity, "salary",
            {"employee_id": change["employee_id"], "change_id": change_id, "closed_months": months},
            f"A salary change approved on {change['approved_on']} is effective {change['effective_date']}, "
            f"inside {len(months)} closed month(s) already paid at the old salary",
            [Evidence(change.kind, change.path, (change.row_no,), f"approved {change['annual_salary']} on "
                      f"{change['approved_on']}, effective {change['effective_date']}")]
            + [Evidence.of(r, f"{r.month}: paid at {r['annual_salary']}") for r in rows],
            ["PAY-03", "FIN-05"], item["arrears"], rows[0]["currency"],
            "sum over closed months of gross at the new salary minus gross paid",
            "hold_then_pay_arrears_in_open_month_with_approval", "controller", src.person_with_role("controller"),
            merge_key=change_id))


# -- Register vs the pay function and the pay policy ----------------------------
def register_vs_engine(src: Sources, out: Findings) -> None:
    last: dict[tuple, Row] = {}
    for month in src.months:
        for entity in src.entities:
            policy_rate = src.pension_rate(entity)
            shortfall, rows_off, evidence = ZERO, 0, []
            for row in src.rows("payroll_register", month, entity):
                keys = YTD_KEYS[entity]
                period_base = {k: row.amount("gross_pay") if k == "gross" else Decimal(row[f"period_{k}"]) for k in keys}
                before = {k: Decimal(row[f"ytd_{k}"]) - period_base[k] for k in keys}
                previous = last.get((entity, row["employee_id"], row["tax_year"]))
                if previous is not None and Decimal(previous["ytd_gross"]) != before["gross"]:
                    out.add(Finding(
                        "register_vs_engine", "year_to_date_discontinuity", month, entity, "statutory",
                        {"employee_id": row["employee_id"], "entity": entity},
                        "Year-to-date gross does not follow on from the previous register",
                        [Evidence.of(previous, f"year to date after {previous.month}: {previous['ytd_gross']}"),
                         Evidence.of(row, f"year to date before {month}: {fmt(before['gross'])}")],
                        ["PAY-03"], before["gross"] - Decimal(previous["ytd_gross"]), row["currency"],
                        "difference in year-to-date gross", "investigate_with_payroll_provider", "finance_manager",
                        src.person_with_role("finance_manager"), merge_key=row["employee_id"]))
                last[(entity, row["employee_id"], row["tax_year"])] = row
                result = calculate_pay(entity, int(row["tax_year"]), int(row["tax_period"]), row.amount("gross_pay"),
                                       before, employer_pension_rate=policy_rate,
                                       days=(int(row["days_paid"]), int(row["days_in_month"])))
                engine = {**result.employee, **result.employer}
                for component, value in engine.items():
                    paid = row.amount(component)
                    if paid == value:
                        continue
                    if component == "employer_pension":
                        shortfall += value - paid
                        rows_off += 1
                        if len(evidence) < 5:
                            evidence.append(Evidence.of(row, f"employer pension {fmt(paid)} at rate "
                                                             f"{row.get('employer_pension_rate')}"))
                        continue
                    out.add(Finding(
                        "register_vs_engine", "payroll_calculation_differs", month, entity, "statutory",
                        {"employee_id": row["employee_id"], "entity": entity, "component": component},
                        f"{component} on the register is {fmt(paid)}; the statutory calculation gives {fmt(value)}",
                        [Evidence.of(row, f"{component} {fmt(paid)}"),
                         Evidence("pay_function", "vouch.calc.pay.calculate_pay", (),
                                  f"{component} {fmt(value)} ({'; '.join(result.citations[:3])}...)")],
                        ["FIN-09"], value - paid, row["currency"], "statutory amount minus register amount",
                        "recompute_and_draft_correction", "finance_manager", src.person_with_role("finance_manager"),
                        merge_key=f"{row['employee_id']}:{component}"))
            if rows_off:
                policy = src.rule("PAY-04")
                out.add(Finding(
                    "register_vs_policy", "contribution_rate_differs_from_policy", month, entity, "rates",
                    {"entity": entity, "period": month, "rows": rows_off, "policy_rate": str(policy_rate)},
                    f"{rows_off} register rows carry an employer pension that is not the policy's {policy_rate}",
                    evidence + [Evidence("pay_policy", "standing/pay_policy.json", (), policy["text"])],
                    ["PAY-04", "FIN-09"] + (["FIN-05"] if src.is_closed(month) else []), shortfall,
                    src.currency(entity), "sum over rows of pension at the policy rate minus pension paid",
                    _action_for_money(src, month, "recompute_and_correct", "recompute_and_draft_correction_in_open_month"),
                    "finance_manager", src.person_with_role("finance_manager"), merge_key=f"{entity}:pension"))


# -- Register vs payment file, and bank details ----------------------------------
def hr_accounts(src: Sources, hr: dict[str, dict[str, Row]]) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """(employee, entity) -> [(month, account)] as HR has held them."""
    held = defaultdict(list)
    for month in src.months:
        for emp, row in hr[month].items():
            held[(emp, row["entity"])].append((month, row["bank_account"]))
    for row in src.rows("legacy_payment_history"):
        held[(row["employee_id"], row["entity"])].append((row["year"], row["bank_account"]))
    return held


def register_vs_payments(src: Sources, hr: dict[str, dict[str, Row]], out: Findings) -> None:
    accounts = hr_accounts(src, hr)
    emails = src.rows("inbox")
    for month in src.months:
        for entity in src.entities:
            register = defaultdict(list)
            for row in src.rows("payroll_register", month, entity):
                register[row["employee_id"]].append(row)
            payments = defaultdict(list)
            for row in src.rows("payment_file", month, entity):
                payments[row["employee_id"]].append(row)
            for emp, rows in sorted(payments.items()):
                owed = register.get(emp, [])
                if not owed:
                    out.add(Finding(
                        "register_vs_payments", "payment_without_register_row", month, entity, "one_payment",
                        {"employee_id": emp, "payment_id": rows[0]["payment_id"]},
                        "A payment has no register row behind it",
                        [Evidence.of(rows[0], f"pays {rows[0]['amount']}"),
                         Evidence("payroll_register", f"months/{month}/payroll_register_{entity}.csv", (), "no row")],
                        ["FIN-08", "FIN-09"], rows[0].amount("amount"), rows[0]["currency"], "payment amount",
                        "hold_payment", "treasury", src.person_with_role("treasury"), merge_key=rows[0]["payment_id"]))
                ordered = sorted(rows, key=lambda r: r["payment_id"])
                for extra in ordered[len(owed):] if owed else []:
                    original = ordered[0]
                    out.add(Finding(
                        "register_vs_payments", "duplicate_payment", month, entity, "duplicate_payment",
                        {"employee_id": emp, "payment_id": extra["payment_id"],
                         "original_payment_id": original["payment_id"]},
                        "The payment file pays the same register row twice",
                        [Evidence.of(original, f"{original['payment_id']} pays {original['amount']}"),
                         Evidence.of(extra, f"{extra['payment_id']} pays {extra['amount']} again"),
                         Evidence.of(owed[0], f"one net payment of {owed[0]['net_pay']} owed")],
                        ["FIN-08"], extra.amount("amount"), extra["currency"], "the repeated payment",
                        "reject_duplicate", "treasury", src.person_with_role("treasury"), merge_key=extra["payment_id"]))
                for payment, reg in zip(ordered, owed):
                    if payment.amount("amount") != reg.amount("net_pay"):
                        out.add(Finding(
                            "register_vs_payments", "payment_differs_from_net_pay", month, entity, "one_payment",
                            {"employee_id": emp, "payment_id": payment["payment_id"]},
                            f"The payment is {payment['amount']}; the register's net pay is {reg['net_pay']}",
                            [Evidence.of(payment, f"pays {payment['amount']}"), Evidence.of(reg, f"net {reg['net_pay']}")],
                            ["FIN-08"], payment.amount("amount") - reg.amount("net_pay"), payment["currency"],
                            "payment minus net pay", "hold_and_correct", "treasury", src.person_with_role("treasury"),
                            merge_key=payment["payment_id"]))
                known_to_hr = any(emp in hr[m] for m in src.months if m <= month)
                for payment in ordered if known_to_hr else []:     # unknown people are already a case
                    held = [a for m, a in accounts.get((emp, entity), []) if m <= month]
                    if payment["bank_account"] in held:
                        continue
                    asked = [e for e in emails if payment["bank_account"] in e.get("body", "")]
                    evidence = [Evidence.of(payment, f"pays to {payment['bank_account']}"),
                                Evidence("hr_census", hr_path(src, month), (),
                                         f"HR holds {held[-1] if held else 'no account'} for this person in {entity}")]
                    evidence += [Evidence.of(e, f"email from {e['from']} on {e['received_at']} asks for "
                                                f"{payment['bank_account']}") for e in asked]
                    out.add(Finding(
                        "register_vs_payments", "bank_details_differ_from_hr", month, entity, "bank_details",
                        {"employee_id": emp, "payment_id": payment["payment_id"],
                         **({"email_id": asked[0]["email_id"]} if asked else {})},
                        "The payment goes to an account HR does not hold for this person"
                        + (" — the change came by email" if asked else ""),
                        evidence, ["PAY-06", "FIN-09"], payment.amount("amount"), payment["currency"], "payment held",
                        "hold_payment_and_ask_hr_for_call_back", "hr", src.person_with_role("hr"),
                        merge_key=payment["payment_id"]))
            for emp, rows in register.items():
                for reg in rows:
                    if reg.amount("net_pay") > 0 and not payments.get(emp):
                        out.add(Finding(
                            "register_vs_payments", "net_pay_without_payment", month, entity, "one_payment",
                            {"employee_id": emp}, "Net pay is owed but the payment file has no payment",
                            [Evidence.of(reg, f"net {reg['net_pay']}"),
                             Evidence("payment_file", f"months/{month}/payment_file_{entity}.csv", (), "no payment")],
                            ["PAY-01"], reg.amount("net_pay"), reg["currency"], "net pay owed", "ask_treasury",
                            "treasury", src.person_with_role("treasury"), merge_key=emp))


def bank_account_history(src: Sources, hr: dict[str, dict[str, Row]], out: Findings) -> None:
    """An account HR holds for one person that belonged to someone else before."""
    owners: dict[str, list[tuple[str, str, Row]]] = defaultdict(list)
    for row in src.rows("legacy_payment_history"):
        owners[row["bank_account"]].append((row["year"], row["employee_id"], row))
    for month in src.months:
        for emp, row in hr[month].items():
            owners[row["bank_account"]].append((month, emp, row))
    for account, held in owners.items():
        people = {}
        for when, emp, row in sorted(held, key=lambda x: x[0]):
            people.setdefault(emp, (when, row))
        if len(people) < 2:
            continue
        first_emp = min(people, key=lambda e: people[e][0])
        for emp, (when, row) in people.items():
            if emp == first_emp or row.kind != "hr_census":
                continue
            payment = next((p for p in src.rows("payment_file", src.open_month)
                            if p["employee_id"] == emp and p["bank_account"] == account), None)
            earlier_when, earlier = people[first_emp]
            out.add(Finding(
                "bank_account_history", "bank_account_belonged_to_someone_else", row.month, row["entity"], "bank_details",
                {"employee_id": emp, "previous_holder_employee_id": first_emp,
                 **({"payment_id": payment["payment_id"]} if payment else {})},
                f"HR gives this person an account that was {first_emp}'s ({earlier_when})",
                [Evidence.of(row, f"HR holds {account}"), Evidence.of(earlier, f"{account} paid {first_emp} in {earlier_when}")],
                ["PAY-06", "FIN-09"], payment.amount("amount") if payment else ZERO, src.currency(row["entity"]),
                "payment to the account held" if payment else "no payment pending",
                "hold_pay_destination_and_ask_hr_for_call_back", "hr", src.person_with_role("hr"),
                merge_key=f"{emp}:{account}"))


# -- Payment file vs bank, and bank vs ledger ----------------------------------
def _ledger_cash_lines(src: Sources):
    """(entry, line, amount, [evidence rows]) for every posted cash line."""
    rows = src.conn.execute("""SELECT l.entry_id, l.line_no, l.debit, l.credit, e.period, e.entity
        FROM journal_lines l JOIN journal_entries e USING (entry_id)
        WHERE l.account = '1000' AND e.status = 'posted'""").fetchall()
    sources = defaultdict(list)
    for s in src.conn.execute("""SELECT s.entry_id, s.line_no, d.kind, d.path, s.doc_id, s.row_no FROM line_sources s
            JOIN documents d USING (doc_id) WHERE s.role = 'evidence'"""):
        sources[(s["entry_id"], s["line_no"])].append(s)
    return [(r, sources[(r["entry_id"], r["line_no"])]) for r in rows]


def _booked_as(src: Sources) -> dict[tuple[str, int], tuple[str, int]]:
    """(entry, line) -> (path, row) of the booked-ledger row a history line was loaded from."""
    return {(s["entry_id"], s["line_no"]): (s["path"], s["row_no"]) for s in src.conn.execute(
        """SELECT s.entry_id, s.line_no, d.path, s.row_no FROM line_sources s JOIN documents d USING (doc_id)
           WHERE s.role = 'booked_as'""")}


def payments_vs_bank(src: Sources, out: Findings) -> None:
    bank = defaultdict(list)
    for row in src.rows("bank_statement"):
        bank[row["reference"]].append(row)
    booked_from_payment_file = {
        (s["doc_id"], s["row_no"]) for _, evidence in _ledger_cash_lines(src) for s in evidence
        if s["kind"] == "payment_file"}
    paid_ids = set()
    for month in src.months:
        for entity in src.entities:
            uncleared = defaultdict(list)
            for payment in src.rows("payment_file", month, entity):
                paid_ids.add(payment["payment_id"])
                if payment["status"] != "released":
                    continue
                lines = bank.get(payment["payment_id"], [])
                if not lines:
                    if (payment.doc_id, payment.row_no) not in booked_from_payment_file:
                        uncleared[payment["batch_id"]].append(payment)
                    continue
                line = lines[0]
                if line.month != month:
                    late = (_d(line["date"]) - _d(payment["value_date"])).days
                    out.add(Finding(
                        "payments_vs_bank", "cleared_after_month_end", month, entity, "cash",
                        {"payment_id": payment["payment_id"], "employee_id": payment["employee_id"]},
                        f"Released on {payment['value_date']}, cleared {line['date']} ({late} days later)",
                        [Evidence.of(payment, f"released {payment['value_date']}"),
                         Evidence.of(line, f"cleared {line['date']}")],
                        ["FIN-06"], payment.amount("amount"), payment["currency"], "payment in transit at month end",
                        "none", None, disposition="explained" if late <= 5 else "case",
                        explanation="Timing difference: the bank cleared it in the first days of the next month.",
                        merge_key=payment["payment_id"]))
                if -line.amount("amount") != payment.amount("amount"):
                    out.add(Finding(
                        "payments_vs_bank", "bank_amount_differs_from_payment", month, entity, "cash",
                        {"payment_id": payment["payment_id"]}, "The bank cleared a different amount than released",
                        [Evidence.of(payment, f"released {payment['amount']}"), Evidence.of(line, f"cleared {line['amount']}")],
                        ["FIN-06"], -line.amount("amount") - payment.amount("amount"), payment["currency"],
                        "cleared minus released", "ask_treasury", "treasury", src.person_with_role("treasury"),
                        merge_key=payment["payment_id"]))
                if len(lines) > 1:
                    out.add(Finding(
                        "payments_vs_bank", "payment_cleared_twice", month, entity, "cash",
                        {"payment_id": payment["payment_id"]}, "The bank shows the same payment more than once",
                        [Evidence.of(l, f"cleared {l['amount']} on {l['date']}") for l in lines],
                        ["FIN-08", "FIN-06"], sum((-l.amount("amount") for l in lines[1:]), ZERO), payment["currency"],
                        "amount cleared more than once", "recover_duplicate", "treasury",
                        src.person_with_role("treasury"), merge_key=payment["payment_id"]))
            for batch, missing in uncleared.items():
                total = sum((p.amount("amount") for p in missing), ZERO)
                released = [p for p in src.rows("payment_file", month, entity) if p["batch_id"] == batch]
                out.add(Finding(
                    "payments_vs_bank", "payments_not_cleared", month, entity, "cash",
                    {"batch_id": batch, "payment_ids": [p["payment_id"] for p in missing],
                     "employee_ids": [p["employee_id"] for p in missing]},
                    f"{len(missing)} of {len(released)} released payments in the batch never reached the bank",
                    [Evidence(missing[0].kind, missing[0].path, tuple(p.row_no for p in missing),
                              f"{len(missing)} payments released, {fmt(total)} in all"),
                     Evidence("bank_statement", f"months/{month}/bank_statement_{entity}.csv", (),
                              "no line for these payments in this or any later statement")],
                    ["FIN-06", "FIN-08"], total, src.currency(entity), "sum of the payments the bank never cleared",
                    "request_release_of_unpaid_lines_only", "treasury", src.person_with_role("treasury"),
                    merge_key=batch))
    for reference, lines in bank.items():
        if reference.startswith("PAY-") and reference not in paid_ids:
            line = lines[0]
            out.add(Finding(
                "payments_vs_bank", "bank_payment_without_payment_file", line.month, line.entity, "cash",
                {"bank_line_id": line["bank_line_id"]}, "The bank paid out against a payment the files never released",
                [Evidence.of(line, f"paid {line['amount']}"),
                 Evidence("payment_file", "months/*/payment_file_*.csv", (), f"no payment {reference}")],
                ["FIN-06"], -line.amount("amount"), line["currency"], "amount paid out", "ask_treasury", "treasury",
                src.person_with_role("treasury"), merge_key=line["bank_line_id"]))


def bank_vs_ledger(src: Sources, out: Findings) -> None:
    booked_bank_rows, booked_as = set(), _booked_as(src)
    for line, evidence in _ledger_cash_lines(src):
        kinds = {e["kind"] for e in evidence}
        amount = Decimal(line["debit"]) - Decimal(line["credit"])
        for e in evidence:
            if e["kind"] == "bank_statement":
                booked_bank_rows.add((e["doc_id"], e["row_no"]))
        if kinds & {"bank_statement", "opening_balances"}:
            continue
        payment = next((e for e in evidence if e["kind"] == "payment_file"), None)
        row = next((r for r in src.rows("payment_file") if payment and (r.doc_id, r.row_no) ==
                    (payment["doc_id"], payment["row_no"])), None)
        booked = booked_as.get((line["entry_id"], line["line_no"]))
        out.add(Finding(
            "bank_vs_ledger", "cash_booked_but_not_in_bank", line["period"], line["entity"], "cash",
            {"entry_id": line["entry_id"], **({"payment_id": row["payment_id"], "employee_id": row["employee_id"]}
                                               if row else {})},
            "The ledger books cash leaving that the bank never shows",
            [Evidence("journal", f"journal:{line['entry_id']}", (line["line_no"],), f"cash {fmt(amount)}"),
             Evidence("bank_statement", f"months/{line['period']}/bank_statement_{line['entity']}.csv", (),
                      "no matching line")]
            + ([Evidence("ledger_booked", booked[0], (booked[1],), f"booked as cash {fmt(amount)}")] if booked else [])
            + ([Evidence.of(row, f"payment {row['payment_id']} released")] if row else []),
            ["FIN-06"], -amount, src.currency(line["entity"]), "cash booked without a bank line",
            "trust_bank_keep_reconciling_item_open", "treasury", src.person_with_role("treasury"),
            merge_key=line["entry_id"]))
    for row in src.rows("bank_statement"):
        if (row.doc_id, row.row_no) not in booked_bank_rows:
            out.add(Finding(
                "bank_vs_ledger", "bank_line_not_booked", row.month, row.entity, "cash",
                {"bank_line_id": row["bank_line_id"], "reference": row["reference"]},
                "Cash moved at the bank but the ledger has no line for it",
                [Evidence.of(row, f"{row['amount']} on {row['date']}"),
                 Evidence("journal", "journal", (), "no cash line rests on this bank line")],
                ["FIN-06"], -row.amount("amount"), row["currency"], "bank movement not booked",
                "draft_cash_entry", "treasury", src.person_with_role("treasury"), merge_key=row["bank_line_id"]))


# -- Equity platform vs signed grants, board approvals and HR ---------------------
def equity_vs_grants(src: Sources, hr: dict[str, dict[str, Row]], out: Findings) -> None:
    signed = {r["grant_id"]: r for r in src.rows("signed_grants")}
    board = {r["grant_id"]: r for r in src.rows("board_approvals")}
    forfeits: dict[str, dict] = {}
    unapproved: dict[str, dict] = {}
    for month in src.months:
        end = dates.month_end(month)
        fx_gap = defaultdict(lambda: [ZERO, []])
        for row in src.rows("equity_report", month):
            grant, emp, status = row["grant_id"], row["employee_id"], row["status"]
            expense = row.amount("expense_month_usd")
            approval = board.get(grant)
            if approval is None and (expense != 0 or status in LIVE_GRANT or row.amount("cumulative_expense_usd") != 0):
                unapproved.setdefault(grant, {"rows": []})["rows"].append(row)
                continue
            h = hr[month].get(emp)
            if h and h["entity"] != row["entity"] and _d(h["entity_since"]) <= end:
                out.add(_entity_mismatch(src, month, emp, h, Evidence.of(row, f"expensed in {row['entity']}"),
                                         row.amount("expense_month_local"), row["local_currency"]))
            if h and h["exit_date"] and _d(h["exit_date"]) <= end and status == "active":
                forfeits.setdefault(grant, {"rows": [], "hr": h})["rows"].append(row)
                continue
            if approval is not None and grant in signed and grant not in forfeits:
                terms = _terms(signed[grant])
                differs = {f for f, v in (("shares", row["shares"]), ("grant_date", row["grant_date"]),
                                          ("fair_value_per_share_usd", row["fair_value_per_share_usd"]),
                                          ("vest_start", row["vest_start"])) if v and v != signed[grant][f]}
                if differs:
                    out.add(Finding(
                        "equity_vs_grants", "grant_terms_differ", month, row["entity"], "grant",
                        {"grant_id": grant, "employee_id": emp, "fields": sorted(differs)},
                        f"The platform's {', '.join(sorted(differs))} differ from the signed grant",
                        [Evidence.of(row, ", ".join(f"{f}={row[f]}" for f in sorted(differs))),
                         Evidence.of(signed[grant], ", ".join(f"{f}={signed[grant][f]}" for f in sorted(differs)))],
                        ["FIN-07", "FIN-09"], row.amount("expense_month_usd"), "USD", "platform expense this month",
                        "ask_stock_admin", "stock_admin", src.person_with_role("stock_admin"), merge_key=grant))
                    continue
                exit_date = _d(h["exit_date"]) if h else None
                expected = stock.month_expense(terms, exit_date, month)
                if expected.expense != expense:
                    out.add(Finding(
                        "equity_vs_grants", "stock_expense_differs", month, row["entity"], "grant",
                        {"grant_id": grant, "employee_id": emp},
                        f"The platform expenses {fmt(expense)}; the signed terms and HR dates give {fmt(expected.expense)}",
                        [Evidence.of(row, f"expense {fmt(expense)}"),
                         Evidence.of(signed[grant], f"{signed[grant]['shares']} shares at "
                                                     f"{signed[grant]['fair_value_per_share_usd']}")],
                        ["FIN-07"], expense - expected.expense, "USD", "platform expense minus expected expense",
                        "correct_stock_expense", "stock_admin", src.person_with_role("stock_admin"),
                        merge_key=grant))
            if row["local_currency"] != "USD":
                try:
                    policy = fx.month_average(row["local_currency"], month)
                except fx.MissingFxRate as missing:
                    out.add(Finding(
                        "equity_vs_grants", "fx_rate_missing", month, row["entity"], "fx",
                        {"entity": row["entity"], "period": month, "currency": row["local_currency"]},
                        f"No complete recorded rate for {row['local_currency']} in {month}: the translation is held",
                        [Evidence.of(row, f"platform translated at {row['fx_rate']}"),
                         Evidence("exchange_rates", "vouch/reference/fx_oxr_usd.csv", (), str(missing))],
                        ["FIN-02"], ZERO, row["local_currency"], "translation held; no amount converted",
                        "hold_conversion_until_the_rate_is_recorded", "controller",
                        src.person_with_role("controller"), merge_key=f"{row['entity']}:{month}"))
                    continue
                gap = row.amount("expense_month_local") - fx.to_local(expense, policy)
                if gap or row["fx_rate"] != str(policy.rate):
                    item = fx_gap[(row["entity"], row["local_currency"], str(policy.rate), policy.citation)]
                    item[0] += gap
                    item[1].append(row)
        for (entity, currency, rate, basis), (gap, rows) in fx_gap.items():
            out.add(Finding(
                "equity_vs_grants", "fx_translation_differs", month, entity, "fx",
                {"entity": entity, "period": month, "rows": len(rows)},
                f"The platform translated at {rows[0]['fx_rate']}; the policy rate is {rate}",
                [Evidence(rows[0].kind, rows[0].path, tuple(r.row_no for r in rows), f"rate {rows[0]['fx_rate']}"),
                 Evidence("exchange_rates", "vouch/reference/fx_oxr_usd.csv", (), basis)],
                ["FIN-02"], gap, currency, "platform local amount minus policy-rate amount", "use_policy_rate",
                "stock_admin", src.person_with_role("stock_admin"), merge_key=f"{entity}:fx"))
    for grant, item in unapproved.items():
        rows = item["rows"]
        last = rows[-1]
        out.add(Finding(
            "equity_vs_grants", "grant_without_board_approval", rows[0].month, last["entity"], "grant",
            {"grant_id": grant, "employee_id": last["employee_id"]},
            "The platform expenses a grant the board has not approved",
            [Evidence.of(last, f"status {last['status']}, resolution '{last['board_resolution']}', "
                               f"expense {last['expense_month_usd']}"),
             Evidence("board_approvals", "standing/board_approvals.csv", (), "no approval for this grant"),
             Evidence("signed_grants", "standing/signed_grants.csv", (),
                      "signed" if grant in signed else "no signed grant")],
            ["FIN-07", "FIN-09"], last.amount("cumulative_expense_usd"), "USD", "expense the platform has recognised",
            "block_expense_and_ask_stock_admin", "stock_admin", src.person_with_role("stock_admin"), merge_key=grant))
    for grant, item in forfeits.items():
        rows, h = item["rows"], item["hr"]
        closed = [r for r in rows if src.is_closed(r.month)] or rows
        reference = closed[-1]
        exit_date = _d(h["exit_date"])
        correct = stock.cumulative_expense(_terms(signed[grant]), exit_date, dates.month_end(reference.month))[3] \
            if grant in signed else ZERO
        reverse = reference.amount("cumulative_expense_usd") - correct
        out.add(Finding(
            "equity_vs_grants", "forfeiture_not_recorded", rows[0].month, rows[0]["entity"], "employment",
            {"grant_id": grant, "employee_id": rows[0]["employee_id"], "exit_date": h["exit_date"]},
            f"HR shows the holder left on {h['exit_date']}; the platform still treats the grant as live",
            [Evidence.of(r, f"{r.month}: status {r['status']}, expense {r['expense_month_usd']}") for r in rows]
            + [Evidence.of(h, f"left on {h['exit_date']}")],
            ["FIN-07", "FIN-05", "FIN-09"], reverse, "USD",
            f"cumulative expense to {reference.month} minus the vested value at exit",
            "reverse_expense_in_open_month", "stock_admin", src.person_with_role("stock_admin"), merge_key=grant))


def _terms(signed: Row) -> stock.GrantTerms:
    return stock.GrantTerms(int(signed["shares"]), Decimal(signed["fair_value_per_share_usd"]),
                            _d(signed["grant_date"]), _d(signed["vest_start"]), int(signed["vest_months"]),
                            int(signed["cliff_months"]))


# -- Approvals vs the finance policy -------------------------------------------
def approvals_vs_policy(src: Sources, out: Findings) -> None:
    controller = src.person_with_role("controller")
    for row in src.rows("approvals_log"):
        problems, rules = [], []
        if row["requested_by"] == row["approved_by"]:
            problems.append("the requester approved their own request")
            rules.append("FIN-03")
        if row.amount("amount") >= src.large_entry_limit(row["currency"]) and row["approved_by"] != controller:
            problems.append(f"it is at or above the large-entry limit and the controller did not approve it")
            rules.append("FIN-04")
        if problems:
            out.add(Finding(
                "approvals_vs_policy", "approval_breaks_policy", row["period"], row["entity"], "approval",
                {"request_id": row["request_id"], "requested_by": row["requested_by"]},
                "This approval breaks policy: " + "; ".join(problems),
                [Evidence.of(row, f"requested by {row['requested_by']}, approved by {row['approved_by']}, "
                                  f"{row['amount']} {row['currency']}"),
                 Evidence("finance_policy", "standing/finance_policy.json", (),
                          " ".join(src.rule(r)["text"] for r in rules))],
                rules, row.amount("amount"), row["currency"], "amount of the request",
                "refuse_and_route_to_controller", "controller", controller, merge_key=row["request_id"]))


ALL_CHECKS = ("hr_vs_register", "salary_vs_register", "register_vs_engine", "register_vs_payments",
              "bank_account_history", "payments_vs_bank", "bank_vs_ledger", "equity_vs_grants", "approvals_vs_policy")


def run_checks(src: Sources) -> Findings:
    out, flagged = Findings(), set()
    hr = hr_by_month(src, out)
    hr_vs_register(src, hr, out, flagged)
    salary_vs_register(src, out, flagged)
    register_vs_engine(src, out)
    register_vs_payments(src, hr, out)
    bank_account_history(src, hr, out)
    payments_vs_bank(src, out)
    bank_vs_ledger(src, out)
    equity_vs_grants(src, hr, out)
    approvals_vs_policy(src, out)
    return out
