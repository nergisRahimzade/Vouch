"""The company's written pay policy and finance policy, issued as standing documents.
Every rule has an id so findings and decisions can cite it."""
from __future__ import annotations

PAY_POLICY = {
    "document": "Northlight Labs Pay Policy",
    "version": "2025.1",
    "effective_from": "2025-01-01",
    "owner": "Head of People Operations",
    "rules": [
        {"rule_id": "PAY-01", "title": "Pay day",
         "text": "Salaries are paid monthly, in arrears, on the last business day (Mon-Fri) of the month."},
        {"rule_id": "PAY-02", "title": "Proration",
         "text": "Pay for a partial month is the monthly salary multiplied by calendar days employed in the entity "
                 "divided by calendar days in the month."},
        {"rule_id": "PAY-03", "title": "Salary source",
         "text": "Only approved salary changes set pay. A change takes effect on its effective date."},
        {"rule_id": "PAY-04", "title": "UK employer pension",
         "text": "The UK entity contributes an employer pension of 5% of each employee's gross pay every period.",
         "employer_pension_rate": {"UK": "0.05"}},
        {"rule_id": "PAY-05", "title": "Leavers",
         "text": "No pay is due for any day after the HR exit date. Any overpayment is recovered from the employee."},
        {"rule_id": "PAY-06", "title": "Bank details",
         "text": "Bank details come only from the HR master. A change requires HR confirmation and a call-back to "
                 "the phone number already on file; an email alone is never sufficient."},
        {"rule_id": "PAY-07", "title": "Entity transfers",
         "text": "An employee moving entity is paid by the new entity, in its currency, from the transfer date; "
                 "the department head confirms the transfer."},
        {"rule_id": "PAY-08", "title": "Tax remittance",
         "text": "Withheld taxes, social contributions and pension are remitted on the 15th of the following month."},
    ],
}

FINANCE_POLICY = {
    "document": "Northlight Labs Finance and Controls Policy",
    "version": "2025.2",
    "effective_from": "2025-01-01",
    "owner": "Controller",
    "rules": [
        {"rule_id": "FIN-01", "title": "Materiality",
         "text": "A reconciliation difference strictly below the threshold for its currency is explained and logged, "
                 "not escalated. A difference at or above the threshold opens a case.",
         "threshold": {"USD": "50.00", "GBP": "40.00", "EUR": "45.00"}},
        {"rule_id": "FIN-02", "title": "Exchange rates",
         "text": "Each month is closed at that month's average exchange rate, the mean of recorded daily rates "
                 "(Open Exchange Rates, USD base) for the month's business days. This one rate translates both "
                 "expenses and balances. A month cannot be closed until every business day of it has a recorded "
                 "rate; an older rate is never reused."},
        {"rule_id": "FIN-03", "title": "Segregation of duties",
         "text": "The requester of an entry or payment can never approve it, and whoever keyed a change can never "
                 "approve it."},
        {"rule_id": "FIN-04", "title": "Large entries",
         "text": "An entry at or above the large-entry limit needs two approvers, one of whom is the controller.",
         "limit": {"USD": "50000.00", "GBP": "40000.00", "EUR": "45000.00"}},
        {"rule_id": "FIN-05", "title": "Period lock",
         "text": "Closed months are locked. Corrections to a closed month are booked in the open month. Only the "
                 "controller may reopen a closed month."},
        {"rule_id": "FIN-06", "title": "Cash",
         "text": "The bank statement is the record of cash. A payment booked but not cleared stays a reconciling "
                 "item until the bank shows it."},
        {"rule_id": "FIN-07", "title": "Stock compensation",
         "text": "Grant-date fair value is expensed straight-line over the 48-month service period (ASC 718). "
                 "Forfeitures are recognised when they occur by reversing expense on unvested awards. No expense is "
                 "recognised for a grant without board approval."},
        {"rule_id": "FIN-08", "title": "Duplicates",
         "text": "A payment, grant or entry that repeats one already recorded is rejected."},
        {"rule_id": "FIN-09", "title": "Systems of record",
         "text": "HR is the record for employment, dates, entity, department and bank details; approved salary "
                 "changes for salary; signed grants and board approvals for grant terms; the bank for cash; this "
                 "policy and the pay policy for rates."},
    ],
}
