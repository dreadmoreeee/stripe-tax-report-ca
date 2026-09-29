import pytest
from conftest import sale

from stripe_tax_report_ca import build_report, geo

E = chr(233)   # e-acute
CIRC = chr(206)  # I-circumflex


@pytest.mark.parametrize("text,code", [
    ("NB", "NB"), ("nb", "NB"), (" N.B. ", "NB"), ("New Brunswick", "NB"),
    ("Nouveau-Brunswick", "NB"), ("ON", "ON"), ("Ontario", "ON"),
    ("QC", "QC"), ("Quebec", "QC"), (f"Qu{E}bec", "QC"), ("CA-QC", "QC"),
    ("British Columbia", "BC"), ("Colombie-Britannique", "BC"), ("BC", "BC"),
    ("AB", "AB"), ("Alberta", "AB"),
    ("Ile-du-Prince-Edouard", "PE"),
    ("PEI", "PE"), ("Prince Edward Island", "PE"),
    (f"Nouvelle-{chr(201)}cosse", "NS"), ("Nova Scotia", "NS"),
    ("Newfoundland and Labrador", "NL"), ("Terre-Neuve-et-Labrador", "NL"),
    ("Yukon", "YT"), ("Nunavut", "NU"), ("Northwest Territories", "NT"),
    ("Manitoba", "MB"), ("Saskatchewan", "SK"),
])
def test_province_from_text(text, code):
    assert geo.province_from_text(text) == code


def test_prince_edward_island_french_name():
    assert geo.province_from_text(f"{chr(206)}le-du-Prince-{chr(201)}douard") == "PE"


def test_every_province_name_and_code_maps_back():
    from canada_sales_tax import PROVINCES
    for code, (en, fr) in PROVINCES.items():
        assert geo.province_from_text(code) == code
        assert geo.province_from_text(en) == code
        assert geo.province_from_text(fr) == code


@pytest.mark.parametrize("text", ["", None, "California", "NY", "Ontario Street", "XX"])
def test_province_from_text_no_match(text):
    assert geo.province_from_text(text) is None


def test_california_is_not_a_province():
    # 'CA' is the country code for Canada and the state code for California
    assert geo.province_from_text("CA") is None


def test_resolve_prefers_shipping_then_customer_then_billing():
    loc = geo.resolve_location([("shipping", "ON", "CA"), ("customer", "QC", "CA"),
                                ("billing", "NB", "CA")])
    assert (loc.province, loc.source) == ("ON", "shipping")
    loc = geo.resolve_location([("shipping", "", ""), ("customer", "", ""),
                                ("billing", "New Brunswick", "")])
    assert (loc.province, loc.source) == ("NB", "billing")


def test_resolve_outside_canada():
    loc = geo.resolve_location([("billing", "NY", "US")])
    assert loc.outside_canada and loc.country == "US" and loc.province is None


def test_resolve_foreign_country_beats_lookalike_state_code():
    # 'NL' is also a Mexican state and 'BC' another: the country decides
    loc = geo.resolve_location([("billing", "NL", "MX")])
    assert loc.outside_canada and loc.province is None


def test_resolve_unrecognized_state_is_reported():
    loc = geo.resolve_location([("billing", "Ontaria", "CA")])
    assert loc.province is None and loc.unrecognized == "Ontaria"


def test_is_canada():
    assert geo.is_canada("CA") and geo.is_canada("Canada") and geo.is_canada("can")
    assert geo.is_canada("US") is False
    assert geo.is_canada("") is None


# ---- through the report

def test_sale_maps_by_province_name(payments):
    rows = [sale("a", state="New Brunswick"), sale("b", state=f"Qu{E}bec", amount="114.98",
                                                     tax="14.98")]
    report = build_report(payments(rows))
    assert {s.province for s in report.summary} == {"NB", "QC"}
    assert report.flagged == []


def test_shipping_state_is_used_when_billing_is_missing(payments):
    row = sale("a", state="", country="")
    row["Shipping Address State"] = "Ontario"
    row["Amount"], row["Tax"] = "113.00", "13.00"
    headers = ["id", "Created date (UTC)", "Amount", "Currency", "Status", "Tax",
               "Card Address State", "Card Address Country", "Shipping Address State"]
    report = build_report(payments([row], headers=headers))
    assert report.summary[0].province == "ON"


def test_default_province_is_used_and_flagged(payments):
    report = build_report(payments([sale("a", state="", country="")]), default_province="NB")
    assert report.summary[0].province == "NB"
    assert [f.code for f in report.flagged[0].flags] == ["province-defaulted"]
    assert str(report.totals[-1].gst_hst_expected) == "15.00"


def test_default_province_accepts_a_name(payments):
    report = build_report(payments([sale("a", state="")]), default_province="Nouveau-Brunswick")
    assert report.options["default_province"] == "NB"


def test_bad_default_province_is_an_error(payments):
    with pytest.raises(ValueError, match="default province"):
        build_report(payments([sale()]), default_province="Narnia")


def test_unknown_province_is_flagged_and_unverified(payments):
    report = build_report(payments([sale("a", state="", country="", tax="15.00")]))
    s = report.summary[0]
    assert s.province == "UNKNOWN"
    assert str(s.gst_hst_expected) == "0.00"
    assert str(s.unverified_collected) == "15.00"
    assert str(s.net_sales) == "100.00"
    assert report.flagged[0].flags[0].code == "province-unknown"
    assert any("no province" in w for w in report.warnings)


def test_unrecognized_state_text_is_flagged(payments):
    report = build_report(payments([sale("a", state="Ontaria", amount="113.00", tax="13.00")]), default_province="ON")
    codes = [f.code for f in report.flagged[0].flags]
    assert codes == ["state-unrecognized", "province-defaulted"]


def test_non_canadian_address_gets_export_hint(payments):
    report = build_report(payments([sale("a", state="NY", country="US", amount="100.00",
                                         tax="0.00")]))
    s = report.summary[0]
    assert s.province == "OUTSIDE-CA"
    assert str(s.gst_hst_expected) == "0.00"
    flag = report.flagged[0].flags[0]
    assert flag.code == "outside-canada"
    assert "HINT ONLY" in flag.message


def test_tax_collected_on_export_is_flagged(payments):
    report = build_report(payments([sale("a", state="WA", country="United States",
                                         amount="105.00", tax="5.00")]))
    assert "tax-on-export" in [f.code for f in report.flagged[0].flags]
