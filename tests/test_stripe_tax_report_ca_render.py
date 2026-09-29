import csv
import io
import json

import pytest
from conftest import sale

from stripe_tax_report_ca import NOTICE, build_report, render, to_csv, to_json, to_markdown

HEADERS = ["id", "Created date (UTC)", "Amount", "Amount Refunded", "Currency", "Status", "Fee",
           "Tax", "Refunded date (UTC)", "Card Address State", "Card Address Country"]


@pytest.fixture
def report(payments):
    rows = [
        sale("a", Fee="3.65"),
        sale("b", state="QC", amount="114.98", tax="14.98", Fee="3.63"),
        sale("c", state="ON", amount="113.00", tax="10.00", Fee="3.58"),     # under-collected
        sale("d", Currency="USD", amount="50.00", tax="0.00", Fee="1.75", state="NY",
             country="US"),
        sale("e", state="", country=""),                                       # unknown
        sale("f", **{"Amount Refunded": "115.00", "Refunded date (UTC)": "2026-03-04"}),
    ]
    return build_report(payments(rows, headers=HEADERS), period="quarter",
                        merchant_province="NB")


def test_notice_text_says_not_tax_advice():
    assert "Not tax advice" in NOTICE


def test_markdown_has_all_sections_and_notice(report):
    md = to_markdown(report)
    assert md.startswith("# Stripe sales tax report (Canada)")
    assert md.count("Not tax advice") >= 2
    for heading in ("## GST/HST by period and province", "## PST / QST (shown separately)",
                    "## Fees and ITC hint", "## Flagged rows", "## Excluded from the totals"):
        assert heading in md
    assert "| 2026-Q1 | NB |" in md
    assert "HINT ONLY" in md and "Stripe tax invoice" in md
    assert "under-collected" in md and "currency-not-cad" in md and "province-unknown" in md
    assert "-3.00" in md   # ON difference


def test_markdown_escapes_pipes(payments):
    r = build_report(payments([sale("a|b", tax="1.00")]))
    assert "a\\|b" in to_markdown(r)


def test_csv_is_parseable_and_carries_notice_on_every_row(report):
    text = to_csv(report)
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows and all(r["notice"] == "Not tax advice" for r in rows)
    kinds = {r["record"] for r in rows}
    assert kinds == {"summary", "total", "flagged", "excluded"}
    nb = next(r for r in rows if r["record"] == "summary" and r["province"] == "NB")
    assert nb["period"] == "2026-Q1" and nb["gst_hst_collected"] == "15.00"
    grand = next(r for r in rows if r["record"] == "total" and r["period"] == "ALL")
    assert grand["province"] == "ALL"
    assert float(grand["itc_hint"]) > 0
    flagged = [r for r in rows if r["record"] == "flagged"]
    assert any("under-collected" in r["flags"] for r in flagged)
    excluded = next(r for r in rows if r["record"] == "excluded")
    assert excluded["currency"] == "USD" and excluded["id"] == "d"


def test_json_is_valid_and_complete(report):
    data = json.loads(to_json(report))
    assert "Not tax advice" in data["notice"]
    assert "Stripe tax invoice" in data["itc_note"]
    assert data["options"]["period"] == "quarter"
    assert data["rows_read"] == 6
    nb = next(s for s in data["summary"] if s["province"] == "NB")
    assert nb["gst_hst_difference"] == "0.00" and nb["sales"] == 2 and nb["refunds"] == 1
    assert data["totals"][-1]["province"] == "ALL"
    codes = {f["code"] for r in data["flagged"] for f in r["flags"]}
    assert {"under-collected", "province-unknown"} <= codes
    assert data["excluded"][0]["currency"] == "USD"
    assert data["excluded_totals"]["USD"]["amount"] == "50.00"


def test_render_dispatch_and_unknown_format(report):
    assert render(report, "markdown") == to_markdown(report)
    assert render(report, "md") == to_markdown(report)
    assert render(report, "csv") == to_csv(report)
    assert render(report, "json") == to_json(report)
    with pytest.raises(ValueError, match="unknown format"):
        render(report, "xml")


def test_itc_shown_as_na_when_not_computable(payments):
    r = build_report(payments([sale("a", Fee="3.65")], headers=HEADERS))
    assert "n/a" in to_markdown(r)


def test_json_output_is_ascii(report):
    to_json(report).encode("ascii")
