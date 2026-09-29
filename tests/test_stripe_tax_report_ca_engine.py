import datetime as dt
from decimal import Decimal

import canada_sales_tax as cst
import pytest
from conftest import sale

from stripe_tax_report_ca import build_report
from stripe_tax_report_ca.engine import parse_date, parse_money


def D(x):
    return Decimal(x)


# ---- parsing

@pytest.mark.parametrize("text,expected", [
    ("115.00", "115.00"), ("1,234.5", "1234.50"), ("$10", "10.00"), ("(5.00)", "-5.00"),
    ("-3.10", "-3.10"), ("CAD 12", "12.00"),
])
def test_parse_money(text, expected):
    assert parse_money(text) == D(expected)


def test_parse_money_blank_and_junk():
    assert parse_money("") is None and parse_money(None) is None
    with pytest.raises(ValueError):
        parse_money("abc")
    with pytest.raises(ValueError):
        parse_money("NaN")


def test_parse_money_minor_units():
    assert parse_money("11500", minor_units=True) == D("115.00")


def test_parse_date_variants():
    utc = dt.timezone.utc
    assert parse_date("2026-02-10 15:00:00", utc) == dt.date(2026, 2, 10)
    assert parse_date("2026-02-10T15:00:00Z", utc) == dt.date(2026, 2, 10)
    assert parse_date("2026-02-10", utc) == dt.date(2026, 2, 10)
    assert parse_date("1770735600", utc) == dt.date(2026, 2, 10)


# ---- collected vs expected

def test_correct_tax_has_no_difference(payments):
    report = build_report(payments([sale("a"), sale("b", state="ON", amount="113.00",
                                                     tax="13.00")]))
    t = report.totals[-1]
    assert t.gst_hst_difference == 0
    assert str(t.gst_hst_collected) == "28.00" and str(t.net_sales) == "200.00"
    assert report.flagged == []


def test_under_collected_is_flagged(payments):
    report = build_report(payments([sale("a", tax="13.00")]))
    row = report.flagged[0]
    assert row.flags[0].code == "under-collected"
    assert report.totals[-1].gst_hst_difference == D("-2.00")
    assert report.under_collected == [row]


def test_over_collected_is_flagged(payments):
    report = build_report(payments([sale("a", tax="17.00")]))
    assert report.flagged[0].flags[0].code == "over-collected"


def test_tolerance_hides_one_cent_rounding(payments):
    report = build_report(payments([sale("a", tax="14.99")]))
    assert report.flagged == []
    strict = build_report(payments([sale("a", tax="14.99")]), tolerance="0")
    assert strict.flagged[0].flags[0].code == "under-collected"


def test_blank_tax_cell_counts_as_zero_and_is_flagged(payments):
    report = build_report(payments([sale("a", tax="")]))
    assert [f.code for f in report.flagged[0].flags] == ["tax-blank", "under-collected"]
    assert str(report.totals[-1].gst_hst_collected) == "0.00"


def test_no_tax_column_warns_once_and_uses_zero(csv_file):
    headers = ["id", "Created (UTC)", "Amount", "Currency", "Card Address State"]
    rows = [["a", "2026-03-01", "115.00", "cad", "NB"], ["b", "2026-03-02", "113.00", "cad", "ON"]]
    report = build_report(csv_file(headers, rows))
    assert sum("no Tax column" in w for w in report.warnings) == 1
    assert str(report.totals[-1].gst_hst_collected) == "0.00"
    assert str(report.totals[-1].gst_hst_expected) == "28.00"
    assert report.flagged == []


def test_expected_tax_is_dated_to_the_sale(payments):
    before = build_report(payments([sale("a", date="2025-03-31 12:00:00", state="NS",
                                         amount="115.00", tax="15.00")]))
    after = build_report(payments([sale("a", date="2025-04-01 12:00:00", state="NS",
                                        amount="114.00", tax="14.00")]))
    assert before.flagged == [] and after.flagged == []
    for amount, date in (("115.00", "2025-03-31"), ("114.00", "2025-04-01")):
        expected = cst.calculate(amount, "NS", dt.date.fromisoformat(date), tax_included=True)
        assert expected.tax_total in (D("15.00"), D("14.00"))


def test_before_rate_table_is_unverified(payments):
    report = build_report(payments([sale("a", date="2019-01-15 12:00:00")]))
    assert report.flagged[0].flags[0].code == "before-rate-table"
    assert str(report.totals[-1].unverified_collected) == "15.00"


def test_pst_and_qst_are_shown_separately(payments):
    report = build_report(payments([
        sale("a", state="QC", amount="114.98", tax="14.98"),
        sale("b", state="BC", amount="112.00", tax="12.00"),
    ]))
    by = {s.province: s for s in report.summary}
    assert str(by["QC"].gst_hst_expected) == "5.00" and str(by["QC"].qst_expected) == "9.98"
    assert str(by["QC"].qst_collected) == "9.98" and by["QC"].pst_expected == 0
    assert str(by["BC"].pst_expected) == "7.00" and str(by["BC"].pst_collected) == "7.00"
    assert by["BC"].qst_expected == 0


def test_single_tax_figure_is_split_by_expected_share(payments):
    # BC: 5 GST + 7 PST expected; only 6.00 collected -> half of each
    report = build_report(payments([sale("a", state="BC", amount="106.00", tax="6.00")]))
    s = report.summary[0]
    assert str(s.gst_hst_collected + s.pst_collected) == "6.00"
    assert s.gst_hst_difference < 0 and s.pst_difference < 0


def test_tax_excluded_mode(payments):
    rows = [sale("a", amount="100.00", tax="15.00")]
    report = build_report(payments(rows), tax_included=False)
    t = report.totals[-1]
    assert str(t.net_sales) == "100.00" and str(t.gross) == "115.00"
    assert t.gst_hst_difference == 0 and report.flagged == []
    assert report.options["tax_included"] is False


def test_tax_included_mode_is_the_default(payments):
    report = build_report(payments([sale("a", amount="115.00", tax="15.00")]))
    t = report.totals[-1]
    assert str(t.net_sales) == "100.00" and str(t.gross) == "115.00"
    # the same numbers read as tax-excluded are wrong
    wrong = build_report(payments([sale("a", amount="115.00", tax="15.00")]), tax_included=False)
    assert wrong.totals[-1].gst_hst_expected == D("17.25")
    assert wrong.flagged[0].flags[0].code == "under-collected"


def test_period_month_and_quarter(payments):
    rows = [sale("a", date="2026-01-31 10:00:00"), sale("b", date="2026-02-01 10:00:00"),
            sale("c", date="2026-04-01 10:00:00")]
    month = build_report(payments(rows), period="month")
    assert [s.period for s in month.summary] == ["2026-01", "2026-02", "2026-04"]
    quarter = build_report(payments(rows), period="quarter")
    assert [s.period for s in quarter.summary] == ["2026-Q1", "2026-Q2"]
    assert quarter.summary[0].sales == 2


def test_bad_period(payments):
    with pytest.raises(ValueError, match="period"):
        build_report(payments([sale()]), period="week")


def test_timezone_moves_a_late_evening_sale(payments):
    rows = [sale("a", date="2026-04-01 01:30:00")]   # 22:30 on 31 March in Halifax
    assert build_report(payments(rows)).summary[0].period == "2026-04"
    halifax = build_report(payments(rows), timezone="America/Halifax", period="month")
    assert halifax.summary[0].period == "2026-03"


def test_unknown_timezone(payments):
    with pytest.raises(ValueError, match="time zone"):
        build_report(payments([sale()]), timezone="Mars/Base")


def test_minor_units(payments):
    rows = [sale("a", amount="11500", tax="1500", **{"Fee": "365"})]
    report = build_report(payments(rows, headers=[
        "id", "Created date (UTC)", "Amount", "Currency", "Status", "Tax", "Fee",
        "Card Address State", "Card Address Country"]), minor_units=True)
    assert str(report.totals[-1].gross) == "115.00" and str(report.totals[-1].fees) == "3.65"


def test_duplicate_ids_across_files_counted_once(payments):
    p1 = payments([sale("a")], name="one.csv")
    p2 = payments([sale("a"), sale("b")], name="two.csv")
    report = build_report([p1, p2])
    assert report.totals[-1].sales == 2
    assert any("duplicate" in why for why in report.skipped)


def test_failed_and_uncaptured_payments_are_skipped(payments):
    rows = [sale("a", Status="Failed"), sale("b", Status="Paid"), sale("c", Status="Canceled")]
    report = build_report(payments(rows))
    assert report.totals[-1].sales == 1
    assert sum(report.skipped.values()) == 2


def test_bad_date_row_is_excluded(payments):
    report = build_report(payments([sale("a", date="soon"), sale("b")]))
    assert report.totals[-1].sales == 1
    assert report.excluded[0].flags[0].code == "bad-date"


# ---- currency

def test_usd_is_flagged_excluded_and_listed_separately(payments):
    rows = [sale("a"), sale("b", Currency="USD", amount="900.00", tax="0.00", Fee="35.40",
                            state="NY", country="US")]
    report = build_report(payments(rows, headers=[
        "id", "Created date (UTC)", "Amount", "Currency", "Status", "Tax", "Fee",
        "Card Address State", "Card Address Country"]))
    assert report.totals[-1].sales == 1 and str(report.totals[-1].gross) == "115.00"
    assert str(report.totals[-1].fees) == "0.00"
    assert [r.id for r in report.excluded] == ["b"]
    assert report.excluded[0].flags[0].code == "currency-not-cad"
    assert report.excluded_totals["USD"]["amount"] == D("900.00")
    assert report.excluded_totals["USD"]["fees"] == D("35.40")


def test_currency_case_and_missing(payments):
    report = build_report(payments([sale("a", Currency="cad"), sale("b", Currency="")]))
    assert report.totals[-1].sales == 2
    assert [f.code for f in report.flagged[0].flags] == ["currency-missing"]


# ---- refunds

def test_full_refund_reverses_sale_in_the_refund_period(payments):
    rows = [sale("a", date="2026-01-20 10:00:00", amount="115.00", tax="15.00",
                 **{"Amount Refunded": "115.00", "Status": "Refunded",
                    "Refunded date (UTC)": "2026-03-05 10:00:00"})]
    report = build_report(payments(rows))
    by = {s.period: s for s in report.summary}
    assert str(by["2026-01"].net_sales) == "100.00" and by["2026-01"].sales == 1
    assert str(by["2026-03"].net_sales) == "-100.00" and by["2026-03"].refunds == 1
    assert str(by["2026-03"].gst_hst_collected) == "-15.00"
    assert str(by["2026-03"].gst_hst_expected) == "-15.00"
    total = report.totals[-1]
    assert total.net_sales == 0 and total.gst_hst_collected == 0 and total.gst_hst_expected == 0


def test_full_refund_same_period_nets_to_zero(payments):
    rows = [sale("a", **{"Amount Refunded": "115.00", "Refunded date (UTC)": "2026-02-20"})]
    report = build_report(payments(rows), period="quarter")
    assert len(report.summary) == 1
    assert report.summary[0].net_sales == 0 and report.summary[0].gst_hst_collected == 0


def test_partial_refund_is_proportional(payments):
    rows = [sale("a", date="2026-01-10 10:00:00", amount="200.00", tax="26.09",
                 **{"Amount Refunded": "50.00", "Refunded date (UTC)": "2026-02-10 10:00:00"})]
    # 200.00 tax-included in NB: net 173.91, tax 26.09; a quarter is refunded
    report = build_report(payments(rows))
    by = {s.period: s for s in report.summary}
    assert by["2026-02"].refunds == 1
    assert str(by["2026-02"].net_sales) == "-43.48"
    assert str(by["2026-02"].gst_hst_expected) == "-6.52"
    assert str(by["2026-02"].gst_hst_collected) == "-6.52"
    assert str(by["2026-02"].gross) == "-50.00"
    total = report.totals[-1]
    assert str(total.net_sales) == "130.43" and str(total.gst_hst_collected) == "19.57"


def test_refund_without_date_uses_sale_period_and_is_flagged(payments):
    rows = [sale("a", **{"Amount Refunded": "115.00"})]
    report = build_report(payments(rows))
    assert [f.code for f in report.flagged[0].flags] == ["refund-date-missing"]
    assert report.totals[-1].net_sales == 0


def test_refund_status_without_amount_assumes_full(payments):
    report = build_report(payments([sale("a", Status="Refunded", **{
        "Refunded date (UTC)": "2026-03-01"})]))
    assert "refund-assumed-full" in [f.code for f in report.flagged[0].flags]
    assert report.totals[-1].net_sales == 0


def test_refund_larger_than_amount_is_capped(payments):
    report = build_report(payments([sale("a", **{
        "Amount Refunded": "500.00", "Refunded date (UTC)": "2026-03-01"})]))
    assert "refund-exceeds-amount" in [f.code for f in report.flagged[0].flags]
    assert report.totals[-1].net_sales == 0


def test_refund_of_usd_charge_is_excluded_too(payments):
    report = build_report(payments([sale("a", Currency="USD", **{
        "Amount Refunded": "115.00", "Refunded date (UTC)": "2026-03-01"})]))
    assert report.totals == [] or report.totals[-1].net_sales == 0
    assert report.excluded_totals["USD"]["refunded"] == D("115.00")


# ---- fees and ITC hint

FEE_HEADERS = ["id", "Created date (UTC)", "Amount", "Currency", "Status", "Tax", "Fee",
               "Card Address State", "Card Address Country"]


def test_fees_are_totalled_and_itc_estimated_from_merchant_province(payments):
    rows = [sale("a", Fee="3.65"), sale("b", Fee="6.90", amount="230.00", tax="30.00")]
    report = build_report(payments(rows, headers=FEE_HEADERS), merchant_province="NB")
    t = report.totals[-1]
    assert str(t.fees) == "10.55"
    # fees include 15% HST: 10.55 * 15 / 115
    assert str(round(t.itc_hint, 2)) == "1.38"
    assert report.itc_complete


def test_itc_hint_can_treat_fees_as_tax_excluded(payments):
    report = build_report(payments([sale("a", Fee="10.00")], headers=FEE_HEADERS),
                          merchant_province="ON", fees_include_tax=False)
    assert round(report.totals[-1].itc_hint, 2) == D("1.30")


def test_itc_only_counts_gst_hst_not_pst(payments):
    report = build_report(payments([sale("a", Fee="10.50")], headers=FEE_HEADERS),
                          merchant_province="BC")
    assert str(round(report.totals[-1].itc_hint, 2)) == "0.50"   # 5% GST only


def test_taxes_on_fee_column_wins_over_the_estimate(payments):
    headers = FEE_HEADERS + ["Taxes On Fee"]
    rows = [sale("a", Fee="10.00", **{"Taxes On Fee": "1.20"})]
    report = build_report(payments(rows, headers=headers), merchant_province="NB")
    assert str(report.totals[-1].itc_hint) == "1.20"


def test_itc_incomplete_without_merchant_province(payments):
    report = build_report(payments([sale("a", Fee="3.65")], headers=FEE_HEADERS))
    assert not report.itc_complete
    assert str(report.totals[-1].fees) == "3.65" and report.totals[-1].itc_hint == 0
    assert any("ITC hint incomplete" in w for w in report.warnings)


def test_bad_merchant_province(payments):
    with pytest.raises(ValueError, match="merchant province"):
        build_report(payments([sale()]), merchant_province="Atlantis")
