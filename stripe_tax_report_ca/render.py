"""Render a Report as Markdown, CSV or JSON. Every format carries the notice."""
from __future__ import annotations

import csv
import io
import json
from decimal import Decimal

from .engine import ITC_NOTE, Report, Row, Summary, q

FORMATS = ("markdown", "csv", "json")

CSV_COLUMNS = [
    "record", "period", "province", "sales", "refunds", "gross", "net_sales",
    "gst_hst_expected", "gst_hst_collected", "gst_hst_difference",
    "pst_expected", "pst_collected", "pst_difference",
    "qst_expected", "qst_collected", "qst_difference",
    "unverified_collected", "fees", "itc_hint",
    "id", "date", "currency", "amount", "flags", "notice",
]


def _m(value: Decimal) -> str:
    return str(q(value))


def _sd(value: Decimal) -> str:
    """Signed difference: +0.50 / -1.20 / 0.00."""
    v = q(value)
    return f"+{v}" if v > 0 else str(v)


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _table(header: list[str], rows: list[list[object]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return out


def _flag_text(row: Row) -> str:
    return "; ".join(f"{f.code}: {f.message}" for f in row.flags)


def to_markdown(report: Report) -> str:
    o = report.options
    lines = ["# Stripe sales tax report (Canada)", "", f"> {report.notice}", ""]
    lines.append(
        f"Files: {', '.join(report.files)} | Rows read: {report.rows_read} | "
        f"Period: {o['period']} | Amounts: "
        f"{'tax-included' if o['tax_included'] else 'tax-excluded'} | Currency: CAD only")
    lines.append("")
    if report.warnings:
        lines += ["## Warnings", ""] + [f"- {w}" for w in report.warnings] + [""]

    lines += ["## GST/HST by period and province", ""]
    rows: list[list[object]] = []
    for per in sorted({s.period for s in report.summary}):
        for s in (x for x in report.summary if x.period == per):
            rows.append(_main_row(s, bold=False))
        rows.append(_main_row(next(t for t in report.totals if t.period == per), bold=True))
    if len(report.totals) > 2:
        rows.append(_main_row(report.totals[-1], bold=True))
    lines += _table(
        ["Period", "Province", "Sales", "Refunds", "Net sales", "GST/HST collected",
         "GST/HST expected", "Difference", "Unverified"], rows)
    lines += ["", "Difference = collected minus expected (negative = under-collected). "
              "Refunds reduce sales and tax in the period of the refund. "
              "Unverified = tax collected on rows whose province is unknown.", ""]

    prov_rows: list[list[object]] = []
    for s in report.summary:
        if any(v != 0 for v in (s.pst_expected, s.pst_collected, s.qst_expected, s.qst_collected)):
            prov_rows.append([
                s.period, s.province, _m(s.pst_collected), _m(s.pst_expected),
                _sd(s.pst_difference), _m(s.qst_collected), _m(s.qst_expected),
                _sd(s.qst_difference)])
    lines += ["## PST / QST (shown separately)", ""]
    if prov_rows:
        lines += _table(
            ["Period", "Province", "PST collected", "PST expected", "PST diff.",
             "QST collected", "QST expected", "QST diff."], prov_rows)
        lines += ["", "The Stripe Tax column is one figure, so it is split between GST/HST and "
                  "PST/QST in proportion to the expected tax of each sale.", ""]
    else:
        lines += ["No PST or QST sales in this data.", ""]

    lines += ["## Fees and ITC hint", ""]
    na = not report.itc_complete and report.totals[-1].itc_hint == 0
    fee_rows = [
        [t.period, _m(t.fees), "n/a" if na else _m(t.itc_hint)] for t in report.totals
        if t.province == "ALL"
    ]
    lines += _table(["Period", "Stripe fees", "ITC hint (GST/HST in fees)"], fee_rows)
    lines += ["", ITC_NOTE, ""]

    lines += ["## Flagged rows", ""]
    if report.flagged:
        lines += _table(
            ["File", "Line", "ID", "Date", "Province", "Amount", "Flags"],
            [[r.source, r.line, r.id, r.date, r.province, r.amount, _flag_text(r)]
             for r in report.flagged])
    else:
        lines.append("None.")
    lines.append("")

    lines += ["## Excluded from the totals", ""]
    if report.excluded:
        lines += _table(
            ["File", "Line", "ID", "Date", "Currency", "Amount", "Fee", "Reason"],
            [[r.source, r.line, r.id, r.date or "", r.currency, r.amount, r.fee or "",
              _flag_text(r)] for r in report.excluded])
        if report.excluded_totals:
            lines.append("")
            lines += _table(
                ["Currency", "Rows", "Amount", "Fees", "Refunded"],
                [[cur, v["rows"], _m(v["amount"]), _m(v["fees"]), _m(v["refunded"])]
                 for cur, v in sorted(report.excluded_totals.items())])
    else:
        lines.append("None.")
    lines.append("")

    if report.skipped:
        lines += ["## Skipped rows", ""]
        lines += [f"- {n} row(s): {why}" for why, n in sorted(report.skipped.items())]
        lines.append("")
    lines += ["---", report.notice, ""]
    return "\n".join(lines)


def _main_row(s: Summary, bold: bool) -> list[object]:
    def b(text: object) -> str:
        return f"**{text}**" if bold else str(text)

    return [
        b(s.period), b(s.province), b(s.sales), b(s.refunds), b(_m(s.net_sales)),
        b(_m(s.gst_hst_collected)), b(_m(s.gst_hst_expected)),
        b(_sd(s.gst_hst_difference)), b(_m(s.unverified_collected)),
    ]


def to_csv(report: Report) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
    w.writeheader()

    def base(record: str) -> dict[str, object]:
        return {"record": record, "notice": "Not tax advice"}

    for record, group in (("summary", report.summary), ("total", report.totals)):
        for s in group:
            d = s.to_dict()
            row = base(record)
            row.update({k: v for k, v in d.items() if k in CSV_COLUMNS})
            w.writerow(row)
    for record, group in (("flagged", report.flagged), ("excluded", report.excluded)):
        for r in group:
            row = base(record)
            row.update({
                "province": r.province, "id": r.id, "date": r.date or "",
                "currency": r.currency, "amount": r.amount if r.amount is not None else "",
                "fees": r.fee if r.fee is not None else "", "flags": _flag_text(r),
            })
            w.writerow(row)
    return buf.getvalue()


def to_json(report: Report) -> str:
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=True) + "\n"


def render(report: Report, fmt: str = "markdown") -> str:
    if fmt in ("markdown", "md"):
        return to_markdown(report)
    if fmt == "csv":
        return to_csv(report)
    if fmt == "json":
        return to_json(report)
    raise ValueError(f"unknown format '{fmt}'; use one of {', '.join(FORMATS)}")
