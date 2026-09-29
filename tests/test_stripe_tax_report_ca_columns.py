import pytest
from conftest import sale

from stripe_tax_report_ca import build_report
from stripe_tax_report_ca.columns import build_colmap, detect_export_type, norm_header


def test_norm_header():
    assert norm_header("Created (UTC)") == "createdutc"
    assert norm_header("customer_email") == "customeremail"
    assert norm_header("\ufeffid") == "id"


def test_detect_payments():
    assert detect_export_type(["id", "Amount", "Amount Refunded", "Fee"]) == "payments"


def test_detect_balance():
    assert detect_export_type(["id", "Type", "Amount", "Fee", "Net", "Currency"]) == "balance"


def test_detect_invoices():
    assert detect_export_type(["id", "Number", "Total", "Amount Paid", "Tax"]) == "invoices"


def test_detect_unknown():
    with pytest.raises(ValueError, match="cannot tell"):
        detect_export_type(["foo", "bar"])


@pytest.mark.parametrize("headers", [
    ["id", "Created date (UTC)", "Amount", "Currency", "Status", "Tax",
     "Card Address State", "Card Address Country", "Customer Email"],
    ["id", "created", "amount", "currency", "status", "tax",
     "customer_address_state", "customer_address_country", "customer_email"],
    ["ID", "Created (UTC)", "AMOUNT", "CURRENCY", "STATUS", "TAX",
     "Shipping Address State", "Shipping Address Country", "Customer Email"],
])
def test_header_variants_all_work(csv_file, headers):
    values = {
        "id": "py_1", "created date (utc)": "2026-02-10 15:00:00", "created": "2026-02-10",
        "created (utc)": "2026-02-10 15:00:00", "amount": "115.00", "currency": "CAD",
        "status": "Paid", "tax": "15.00", "card address state": "NB", "card address country": "CA",
        "customer_address_state": "New Brunswick", "customer_address_country": "Canada",
        "shipping address state": "NB", "shipping address country": "CA",
        "customer email": "a@example.com", "customer_email": "a@example.com",
    }
    path = csv_file(headers, [[values[h.lower()] for h in headers]])
    report = build_report(path)
    total = report.totals[-1]
    assert total.sales == 1
    assert str(total.gst_hst_collected) == "15.00"
    assert report.summary[0].province == "NB"
    assert report.flagged == []


def test_colmap_finds_state_variants():
    m = build_colmap(["Amount", "Customer Address State", "Shipping Address State",
                      "Card Address State"], "payments")
    assert m["customer_state"] == "Customer Address State"
    assert m["shipping_state"] == "Shipping Address State"
    assert m["billing_state"] == "Card Address State"


def test_invoice_export_uses_total_and_paid_only(csv_file):
    headers = ["id", "Number", "Customer Email", "Total", "Amount Due", "Tax", "Status",
               "Created (UTC)", "Customer Address State"]
    rows = [
        ["in_1", "0001", "a@example.com", "113.00", "0.00", "13.00", "paid",
         "2026-03-02 10:00:00", "ON"],
        ["in_2", "0002", "b@example.com", "50.00", "50.00", "0.00", "open",
         "2026-03-03 10:00:00", "ON"],
    ]
    report = build_report(csv_file(headers, rows))
    assert report.options["export_types"] == ["invoices"]
    assert report.totals[-1].sales == 1
    assert str(report.totals[-1].net_sales) == "100.00"
    assert report.skipped == {"status open": 1}


def test_balance_export_charges_and_refunds(csv_file):
    headers = ["id", "Type", "Amount", "Fee", "Net", "Currency", "Created (UTC)"]
    rows = [
        ["txn_1", "charge", "115.00", "3.64", "111.36", "cad", "2026-04-01 12:00:00"],
        ["txn_2", "refund", "-115.00", "0.00", "-115.00", "cad", "2026-05-01 12:00:00"],
        ["txn_3", "payout", "-100.00", "0.00", "-100.00", "cad", "2026-05-02 12:00:00"],
    ]
    report = build_report(csv_file(headers, rows), default_province="NB")
    assert report.options["export_types"] == ["balance"]
    by_period = {t.period: t for t in report.totals if t.province == "ALL"}
    assert str(by_period["2026-04"].net_sales) == "100.00"
    assert str(by_period["2026-05"].net_sales) == "-100.00"
    assert by_period["2026-05"].refunds == 1
    assert str(report.totals[-1].net_sales) == "0.00"
    assert any("payout" in why for why in report.skipped)
    assert any("no Tax column" in w for w in report.warnings)


def test_missing_amount_column_is_an_error(csv_file):
    path = csv_file(["id", "Amount Refunded", "Created (UTC)"], [["a", "0", "2026-01-01"]])
    with pytest.raises(ValueError, match="no Amount column"):
        build_report(path, export_type="payments")


def test_blank_amount_row_is_excluded(payments):
    report = build_report(payments([sale(amount="")]))
    assert report.totals[-1].sales == 0
    assert report.excluded[0].flags[0].code == "bad-amount"


def test_bom_and_blank_lines(tmp_path):
    p = tmp_path / "bom.csv"
    p.write_bytes(
        b"\xef\xbb\xbfid,Created (UTC),Amount,Currency,Tax,Card Address State\n"
        b"a,2026-01-05 10:00:00,115.00,cad,15.00,NB\n,,,,,\n")
    assert build_report(p).totals[-1].sales == 1


def test_sale_helper_default_is_clean(payments):
    assert build_report(payments([sale()])).flagged == []
