"""T2: two hand-checked payslips per country, to the cent.

Each expected figure below was worked out by hand from the published rules, step by step in
the comments, without calling Vouch's engine. They have not yet been confirmed with the
official government calculators (IRS withholding estimator, HMRC PAYE tools, BMF
Lohnsteuerrechner); that check is listed as pending in the README."""
from decimal import Decimal as D
from pathlib import Path

from vouch.calc.pay import calculate_pay

ROOT = Path(__file__).resolve().parents[1]


def test_us_ordinary_month_2026():
    # gross 10,000.00 monthly -> annual 120,000; less line 1g 8,600 = 111,400
    # 2026 Single row 57,900-113,200: 5,800 + (111,400 - 57,900) x 22% = 5,800 + 11,770 = 17,570
    # monthly = 17,570 / 12 = 1,464.1667 -> 1,464.17
    # Social Security 10,000 x 6.2% = 620.00 (YTD 20,000, far below the 184,500 base)
    # Medicare 10,000 x 1.45% = 145.00; Additional Medicare: YTD 30,000 < 200,000 -> 0
    # FUTA: the 7,000 base was used up earlier in the year -> 0
    # net = 10,000 - 1,464.17 - 620.00 - 145.00 = 7,770.83
    r = calculate_pay("US", 2026, 3, D("10000.00"),
                      {"gross": D("20000"), "ss_wages": D("20000"), "medicare_wages": D("20000"), "futa_wages": D("7000")})
    assert r.employee == {"federal_income_tax": D("1464.17"), "employee_social_security": D("620.00"),
                          "employee_medicare": D("145.00"), "employee_additional_medicare": D("0.00")}
    assert r.employer == {"employer_social_security": D("620.00"), "employer_medicare": D("145.00"),
                          "employer_futa": D("0.00")}
    assert r.net == D("7770.83")


def test_us_wage_base_and_additional_medicare_2026():
    # gross 25,000.00 -> annual 300,000 - 8,600 = 291,400
    # row 263,725-648,100: 58,448 + (291,400 - 263,725) x 35% = 58,448 + 9,686.25 = 68,134.25
    # monthly = 68,134.25 / 12 = 5,677.854 -> 5,677.85
    # Social Security: only 184,500 - 180,000 = 4,500 left under the base -> 4,500 x 6.2% = 279.00 (each side)
    # Medicare 25,000 x 1.45% = 362.50 (each side)
    # Additional Medicare: YTD goes 180,000 -> 205,000; 5,000 above 200,000 x 0.9% = 45.00 (employee only)
    # net = 25,000 - 5,677.85 - 279.00 - 362.50 - 45.00 = 18,635.65
    r = calculate_pay("US", 2026, 10, D("25000.00"),
                      {"gross": D("180000"), "ss_wages": D("180000"), "medicare_wages": D("180000"), "futa_wages": D("7000")})
    assert r.employee == {"federal_income_tax": D("5677.85"), "employee_social_security": D("279.00"),
                          "employee_medicare": D("362.50"), "employee_additional_medicare": D("45.00")}
    assert r.employer == {"employer_social_security": D("279.00"), "employer_medicare": D("362.50"),
                          "employer_futa": D("0.00")}
    assert r.net == D("18635.65")


def test_uk_basic_rate_2025_26():
    # 1257L month 1: free pay (1257 x 10 + 9) / 12 = 1,048.25; taxable 3,000 - 1,048.25 = 1,951.75
    # all within the basic band (37,700 / 12 = 3,141.67): 1,951.75 x 20% = 390.35
    # employee NI (3,000 - 1,048) x 8% = 156.16; employer NI (3,000 - 417) x 15% = 387.45
    # employer pension 3,000 x 5% = 150.00
    # net = 3,000 - 390.35 - 156.16 = 2,453.49
    r = calculate_pay("UK", 2025, 6, D("3000.00"), {"gross": D("15000")}, employer_pension_rate=D("0.05"))
    assert r.employee == {"paye_income_tax": D("390.35"), "employee_ni": D("156.16")}
    assert r.employer == {"employer_ni": D("387.45"), "employer_pension": D("150.00")}
    assert r.net == D("2453.49")


def test_uk_additional_rate_2026_27():
    # taxable 12,000 - 1,048.25 = 10,951.75
    # basic: 3,141.6667 x 20% = 628.3333
    # higher: up to (125,140 - 12,570) / 12 = 9,380.8333 -> (9,380.8333 - 3,141.6667) x 40% = 2,495.6667
    # additional: (10,951.75 - 9,380.8333) x 45% = 706.9125
    # total 3,830.9125 -> rounded down to the penny 3,830.91
    # employee NI (4,189 - 1,048) x 8% = 251.28 + (12,000 - 4,189) x 2% = 156.22 -> 407.50
    # employer NI (12,000 - 417) x 15% = 1,737.45; pension 600.00
    # net = 12,000 - 3,830.91 - 407.50 = 7,761.59
    r = calculate_pay("UK", 2026, 2, D("12000.00"), {"gross": D("12000")}, employer_pension_rate=D("0.05"))
    assert r.employee == {"paye_income_tax": D("3830.91"), "employee_ni": D("407.50")}
    assert r.employer == {"employer_ni": D("1737.45"), "employer_pension": D("600.00")}
    assert r.net == D("7761.59")


def test_de_ordinary_month_2026():
    # below both ceilings (YTD after 2 months: 10,000 of 101,400 x 90/360 and 69,750 x 90/360)
    # RV 5,000 x 18.6% / 2 = 465.00; AV 5,000 x 2.6% / 2 = 65.00
    # KV 5,000 x 17.5% / 2 = 437.50; PV 5,000 x 3.6% / 2 = 90.00 (each side)
    # taxable: 60,000 - 1,230 - 36 - (465 + 437.50 + 90) x 12 = 46,824
    # 2026 zone 3: z = (46,824 - 17,799) / 10,000 = 2.9025
    #   (173.10 x 2.9025 + 2,397) x 2.9025 + 1,034.87 = 2,899.42275 x 2.9025 + 1,034.87 = 9,450.44 -> 9,450
    # monthly wage tax 9,450 / 12 = 787.50; solidarity: 9,450 <= 20,350 exemption -> 0
    # net = 5,000 - 787.50 - 465 - 65 - 437.50 - 90 = 3,155.00
    r = calculate_pay("DE", 2026, 3, D("5000.00"),
                      {"gross": D("10000"), "rv_base": D("10000"), "kv_base": D("10000"), "sv_days": D("60")}, days=(31, 31))
    assert r.employee == {"lohnsteuer": D("787.50"), "solidaritaetszuschlag": D("0.00"), "employee_rv": D("465.00"),
                          "employee_av": D("65.00"), "employee_kv": D("437.50"), "employee_pv": D("90.00")}
    assert r.employer == {"employer_rv": D("465.00"), "employer_av": D("65.00"), "employer_kv": D("437.50"),
                          "employer_pv": D("90.00")}
    assert r.net == D("3155.00")


def test_de_ceilings_and_solidarity_december_2025():
    # December, 330 social-insurance days already used at the ceilings:
    #   RV/AV room = 96,600 x 360/360 - 88,550 = 8,050; KV/PV room = 66,150 - 60,637.50 = 5,512.50
    # RV 8,050 x 9.3% = 748.65; AV 8,050 x 1.3% = 104.65
    # KV 5,512.50 x 8.55% = 471.32 (471.31875); PV 5,512.50 x 1.8% = 99.23 (99.225, half up)
    # taxable: 108,000 - 1,266 - (748.65 + 471.32 + 99.23) x 12 = 108,000 - 1,266 - 15,830.40 = 90,903.60 -> 90,903
    # 2025 zone 4: 0.42 x 90,903 - 10,911.92 = 27,267.34 -> 27,267; monthly 2,272.25
    # solidarity: min(27,267 x 5.5% = 1,499.69, (27,267 - 19,950) x 11.9% = 870.72) = 870.723 / 12 = 72.56
    # net = 9,000 - 2,272.25 - 72.56 - 748.65 - 104.65 - 471.32 - 99.23 = 5,231.34
    r = calculate_pay("DE", 2025, 12, D("9000.00"),
                      {"gross": D("99000"), "rv_base": D("88550"), "kv_base": D("60637.50"), "sv_days": D("330")},
                      days=(31, 31))
    assert r.employee == {"lohnsteuer": D("2272.25"), "solidaritaetszuschlag": D("72.56"), "employee_rv": D("748.65"),
                          "employee_av": D("104.65"), "employee_kv": D("471.32"), "employee_pv": D("99.23")}
    assert r.employer == {"employer_rv": D("748.65"), "employer_av": D("104.65"), "employer_kv": D("471.32"),
                          "employer_pv": D("99.23")}
    assert r.net == D("5231.34")
    assert r.bases["rv_base"] == D("8050.00") and r.bases["kv_base"] == D("5512.50")


def test_pay_math_lives_only_in_the_engine():
    """Nothing outside vouch/calc reads the rate table or the engine's private parts."""
    for path in (ROOT / "vouch").rglob("*.py"):
        if (ROOT / "vouch" / "calc") in path.parents:
            continue
        text = path.read_text(encoding="utf-8")
        assert "tax_rates.json" not in text and "calc.rates" not in text and "income_tax_de" not in text, path
