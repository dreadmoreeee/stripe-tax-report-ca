"""Read Stripe CSV exports and compare the tax collected with the tax expected."""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import canada_sales_tax as cst

from . import columns, geo

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
GROUPS = ("gst_hst", "pst", "qst")

NOTICE = (
    "Not tax advice. This report is a bookkeeping aid: expected amounts come from "
    "published rates and may not match your real obligations. Have your bookkeeper "
    "or accountant check it before you file."
)
ITC_NOTE = (
    "HINT ONLY: the input tax credit (ITC) figure is an estimate of the GST/HST in "
    "Stripe's fees. The bookkeeper must check the Stripe tax invoice for the real amount."
)
EXPORT_NOTE = (
    "HINT ONLY: this address is outside Canada. Some exports are zero-rated (0% GST/HST), "
    "but it depends on what you sold; confirm the rule for this supply."
)

_PAYMENT_OK = {"paid", "succeeded", "refunded", "partiallyrefunded", "captured"}
_INVOICE_OK = {"paid"}
_BALANCE_SALE = {"charge", "payment"}
_BALANCE_REFUND = {"refund", "paymentrefund"}


def q(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def group_of(code: str) -> str:
    if code in ("GST", "HST"):
        return "gst_hst"
    if code == "QST":
        return "qst"
    return "pst"


# ---------------------------------------------------------------- data model

@dataclass
class Flag:
    code: str
    severity: str  # "warning" or "info"
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "severity": self.severity, "message": self.message}


@dataclass
class Entry:
    """One contribution to a period: a sale, or a (partial) refund of a sale."""
    period: str
    date: dt.date
    province: str
    kind: str                       # "sale" or "refund"
    gross: Decimal
    net_sales: Decimal
    expected: dict[str, Decimal]    # by group, cents
    collected: dict[str, Decimal]   # by group, cents (allocated by expected share)
    unverified: Decimal             # tax collected that could not be compared
    fee: Decimal = ZERO
    itc: Decimal | None = None      # None: no ITC estimate possible


@dataclass
class Row:
    """A source row and what became of it."""
    source: str
    line: int
    id: str
    date: dt.date | None
    currency: str
    amount: Decimal | None
    fee: Decimal | None
    province: str
    flags: list[Flag] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source, "line": self.line, "id": self.id,
            "date": self.date.isoformat() if self.date else "",
            "currency": self.currency,
            "amount": str(self.amount) if self.amount is not None else "",
            "fee": str(self.fee) if self.fee is not None else "",
            "province": self.province,
            "flags": [f.to_dict() for f in self.flags],
        }


@dataclass
class Summary:
    period: str
    province: str
    sales: int = 0
    refunds: int = 0
    gross: Decimal = ZERO
    net_sales: Decimal = ZERO
    gst_hst_expected: Decimal = ZERO
    gst_hst_collected: Decimal = ZERO
    pst_expected: Decimal = ZERO
    pst_collected: Decimal = ZERO
    qst_expected: Decimal = ZERO
    qst_collected: Decimal = ZERO
    unverified_collected: Decimal = ZERO
    fees: Decimal = ZERO
    itc_hint: Decimal = ZERO

    @property
    def gst_hst_difference(self) -> Decimal:
        return self.gst_hst_collected - self.gst_hst_expected

    @property
    def pst_difference(self) -> Decimal:
        return self.pst_collected - self.pst_expected

    @property
    def qst_difference(self) -> Decimal:
        return self.qst_collected - self.qst_expected

    @property
    def tax_difference(self) -> Decimal:
        return self.gst_hst_difference + self.pst_difference + self.qst_difference

    def add(self, e: Entry) -> None:
        if e.kind == "sale":
            self.sales += 1
        else:
            self.refunds += 1
        self.gross += e.gross
        self.net_sales += e.net_sales
        self.gst_hst_expected += e.expected["gst_hst"]
        self.gst_hst_collected += e.collected["gst_hst"]
        self.pst_expected += e.expected["pst"]
        self.pst_collected += e.collected["pst"]
        self.qst_expected += e.expected["qst"]
        self.qst_collected += e.collected["qst"]
        self.unverified_collected += e.unverified
        self.fees += e.fee
        if e.itc is not None:
            self.itc_hint += e.itc

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"period": self.period, "province": self.province,
                               "sales": self.sales, "refunds": self.refunds}
        for name in ("gross", "net_sales", "gst_hst_expected", "gst_hst_collected"):
            out[name] = str(q(getattr(self, name)))
        out["gst_hst_difference"] = str(q(self.gst_hst_difference))
        for name in ("pst_expected", "pst_collected"):
            out[name] = str(q(getattr(self, name)))
        out["pst_difference"] = str(q(self.pst_difference))
        for name in ("qst_expected", "qst_collected"):
            out[name] = str(q(getattr(self, name)))
        out["qst_difference"] = str(q(self.qst_difference))
        out["unverified_collected"] = str(q(self.unverified_collected))
        out["fees"] = str(q(self.fees))
        out["itc_hint"] = str(q(self.itc_hint))
        return out


@dataclass
class Report:
    options: dict[str, Any]
    summary: list[Summary]            # per period and province
    totals: list[Summary]             # per period (province "ALL"), then the grand total
    flagged: list[Row]                # included rows that carry flags
    excluded: list[Row]               # rows left out of every total (non-CAD, unreadable)
    excluded_totals: dict[str, dict[str, Decimal]]
    skipped: dict[str, int]           # rows ignored on purpose, by reason
    warnings: list[str]
    files: list[str]
    rows_read: int = 0
    itc_complete: bool = True
    notice: str = NOTICE

    @property
    def under_collected(self) -> list[Row]:
        return [r for r in self.flagged if any(f.code == "under-collected" for f in r.flags)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "notice": self.notice,
            "itc_note": ITC_NOTE,
            "options": self.options,
            "files": self.files,
            "rows_read": self.rows_read,
            "warnings": self.warnings,
            "summary": [s.to_dict() for s in self.summary],
            "totals": [s.to_dict() for s in self.totals],
            "flagged": [r.to_dict() for r in self.flagged],
            "excluded": [r.to_dict() for r in self.excluded],
            "excluded_totals": {
                cur: {k: str(v) if isinstance(v, Decimal) else v for k, v in vals.items()}
                for cur, vals in self.excluded_totals.items()
            },
            "skipped": self.skipped,
        }


# ------------------------------------------------------------------ parsing

def parse_money(text: str | None, minor_units: bool = False) -> Decimal | None:
    """'1,234.50' -> Decimal('1234.50'); blank -> None; junk -> ValueError."""
    if text is None:
        return None
    s = text.strip()
    if not s:
        return None
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    s = s.replace(" ", "")
    s = re.sub(r"^[A-Za-z]{3}(?=[\d.\-])", "", s)
    s = s.replace("$", "").replace(",", "").replace(" ", "").replace("\u00a0", "")
    try:
        d = Decimal(s)
    except Exception:
        raise ValueError(f"not a number: {text!r}") from None
    if not d.is_finite():
        raise ValueError(f"not a number: {text!r}")
    if negative:
        d = -d
    if minor_units:
        d = d / 100
    return q(d)


def parse_date(text: str, tz: dt.tzinfo) -> dt.date:
    """ISO date, ISO datetime (UTC unless it carries an offset) or epoch seconds."""
    s = text.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return dt.date.fromisoformat(s)
    if re.fullmatch(r"\d{9,11}", s):
        moment = dt.datetime.fromtimestamp(int(s), dt.timezone.utc)
    else:
        if s.endswith(("Z", "z")):
            s = s[:-1] + "+00:00"
        moment = dt.datetime.fromisoformat(s)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(tz).date()


def period_key(d: dt.date, period: str) -> str:
    if period == "quarter":
        return f"{d.year}-Q{(d.month - 1) // 3 + 1}"
    return f"{d.year}-{d.month:02d}"


def _norm_value(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _read_source(src: Any) -> tuple[str, list[str], list[dict[str, str]]]:
    if isinstance(src, (str, Path)):
        name = str(src)
        with open(src, "r", encoding="utf-8-sig", newline="") as fh:
            text = fh.read()
    else:
        name = getattr(src, "name", "<stream>")
        text = src.read()
        if isinstance(text, bytes):
            text = text.decode("utf-8-sig")
        text = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if not reader.fieldnames:
        raise ValueError(f"{name}: empty file, no header row")
    rows = [r for r in reader if any((v or "").strip() for v in r.values() if isinstance(v, str))]
    return str(name), list(reader.fieldnames), rows


# ------------------------------------------------------------------- engine

class _Builder:
    def __init__(self, opts: dict[str, Any], tz: dt.tzinfo) -> None:
        self.o = opts
        self.tz = tz
        self.tol = Decimal(str(opts["tolerance"]))
        self.entries: list[Entry] = []
        self.flagged: list[Row] = []
        self.excluded: list[Row] = []
        self.excluded_totals: dict[str, dict[str, Decimal]] = {}
        self.skipped: Counter[str] = Counter()
        self.warnings: list[str] = []
        self.seen_ids: set[tuple[str, str]] = set()
        self.rows_read = 0
        self.itc_missing_rows = 0
        self._rate_cache: dict[tuple[str, dt.date], Decimal] = {}

    # -- helpers
    def _itc_rate(self, province: str, on: dt.date) -> Decimal | None:
        if on < cst.EARLIEST_DATE:
            return None
        key = (province, on)
        if key not in self._rate_cache:
            pct = sum(
                (Decimal(r.percent) for r in cst.rates(province, on) if r.code in ("GST", "HST")),
                Decimal(0),
            )
            self._rate_cache[key] = pct
        return self._rate_cache[key]

    def _itc(self, fee: Decimal, tax_on_fee: Decimal | None, on: dt.date) -> Decimal | None:
        if tax_on_fee is not None:
            return tax_on_fee
        merchant = self.o["merchant_province"]
        if merchant is None:
            return None
        pct = self._itc_rate(merchant, on)
        if pct is None:
            return None
        if self.o["fees_include_tax"]:
            return fee * pct / (100 + pct)
        return fee * pct / 100

    def _allocate(self, expected: dict[str, Decimal], collected: Decimal) -> dict[str, Decimal]:
        """Split the single Tax figure by the share each tax has in the expected tax."""
        out = {g: ZERO for g in GROUPS}
        total = sum(expected.values(), ZERO)
        if total == 0:
            out["gst_hst"] = collected
            return out
        gst = q(collected * expected["gst_hst"] / total)
        rest = collected - gst
        provincial = expected["pst"] + expected["qst"]
        out["gst_hst"] = gst
        if provincial != 0:
            pst = q(rest * expected["pst"] / provincial)
            out["pst"] = pst
            out["qst"] = rest - pst
        return out

    def _scale(self, e: Entry, ratio: Decimal, date: dt.date, kind: str = "refund") -> Entry:
        def part(v: Decimal) -> Decimal:
            return -q(v * ratio)

        return Entry(
            period=period_key(date, self.o["period"]), date=date, province=e.province,
            kind=kind, gross=part(e.gross), net_sales=part(e.net_sales),
            expected={g: part(v) for g, v in e.expected.items()},
            collected={g: part(v) for g, v in e.collected.items()},
            unverified=part(e.unverified),
        )

    # -- one row
    def process(self, export_type: str, name: str, line: int, row: dict[str, str],
                colmap: dict[str, str]) -> None:
        self.rows_read += 1

        def cell(f: str) -> str:
            h = colmap.get(f)
            return (row.get(h) or "").strip() if h else ""

        rid = cell("id")
        if rid:
            key = (export_type, rid)
            if key in self.seen_ids:
                self.skipped["duplicate id (row repeated across files)"] += 1
                return
            self.seen_ids.add(key)

        # what kind of row is it?
        kind = "sale"
        status = _norm_value(cell("status"))
        if export_type == "balance":
            typ = _norm_value(cell("type"))
            if typ in _BALANCE_REFUND:
                kind = "refund"
            elif typ not in _BALANCE_SALE:
                self.skipped[f"not a sale or refund (type {cell('type') or 'blank'})"] += 1
                return
        else:
            ok = _INVOICE_OK if export_type == "invoices" else _PAYMENT_OK
            if status and status not in ok:
                self.skipped[f"status {cell('status')}"] += 1
                return
            if _norm_value(cell("captured")) == "false":
                self.skipped["not captured"] += 1
                return

        row_rec = Row(name, line, rid, None, "", None, None, "")
        minor = self.o["minor_units"]

        def bad(code: str, message: str) -> None:
            row_rec.flags.append(Flag(code, "warning", message))
            self.excluded.append(row_rec)

        # amount, fee, currency
        try:
            amount = parse_money(cell("amount"), minor)
            fee = parse_money(cell("fee"), minor)
            tax_cell = parse_money(cell("tax"), minor)
            refunded = parse_money(cell("amount_refunded"), minor)
            tax_on_fee = parse_money(cell("taxes_on_fee"), minor)
        except ValueError as exc:
            bad("bad-amount", f"{exc}; row left out of every total")
            return
        if amount is None:
            bad("bad-amount", "the amount is blank; row left out of every total")
            return
        row_rec.amount = amount
        row_rec.fee = fee
        currency = cell("currency").upper()
        row_rec.currency = currency or "CAD"
        if not currency:
            row_rec.flags.append(Flag("currency-missing", "warning",
                                      "no currency; assumed CAD"))
        try:
            date = parse_date(cell("created"), self.tz) if cell("created") else None
        except ValueError:
            date = None
        if date is None:
            bad("bad-date", "the date is missing or unreadable; row left out of every total")
            return
        row_rec.date = date

        if currency and currency != "CAD":
            totals = self.excluded_totals.setdefault(
                currency, {"rows": 0, "amount": ZERO, "fees": ZERO, "refunded": ZERO})
            totals["rows"] += 1
            totals["amount"] += amount
            totals["fees"] += fee or ZERO
            totals["refunded"] += refunded or ZERO
            row_rec.flags.append(Flag(
                "currency-not-cad", "warning",
                f"{currency} is not CAD: left out of every total (this tool is CAD only; "
                "report it separately using CAD values)"))
            self.excluded.append(row_rec)
            return

        # where is the customer?
        groups = [
            (label, cell(f"{label}_state"), cell(f"{label}_country"))
            for label, _s, _c in columns.LOCATION_GROUPS
        ]
        loc = geo.resolve_location(groups)
        inclusive = self.o["tax_included"]
        expected: dict[str, Decimal] = {g: ZERO for g in GROUPS}
        verified = True
        if loc.outside_canada:
            province = geo.OUTSIDE
            row_rec.flags.append(Flag(
                "outside-canada", "info", f"{loc.country}: {EXPORT_NOTE}"))
        elif loc.province:
            province = loc.province
        else:
            if loc.unrecognized:
                row_rec.flags.append(Flag(
                    "state-unrecognized", "warning",
                    f"'{loc.unrecognized}' is not a Canadian province or territory"))
            default = self.o["default_province"]
            if default:
                province = default
                row_rec.flags.append(Flag(
                    "province-defaulted", "info",
                    f"no province in the row; assumed {default} (--default-province)"))
            else:
                province = geo.UNKNOWN
                verified = False
                row_rec.flags.append(Flag(
                    "province-unknown", "warning",
                    "no province in the row and no default: expected tax not computed"))
        row_rec.province = province

        # tax collected
        has_tax_col = "tax" in colmap
        if has_tax_col and tax_cell is None:
            row_rec.flags.append(Flag(
                "tax-blank", "warning", "the Tax cell is empty; collected tax taken as 0"))
        collected = tax_cell if tax_cell is not None else ZERO
        sign = 1
        if kind == "refund":  # standalone refund row (balance export): work on positives
            sign = -1
            amount = abs(amount)
            collected = abs(collected)

        # expected tax, dated to the sale
        net = amount
        if province in geo.PROVINCES_SET and verified:
            if date < cst.EARLIEST_DATE:
                verified = False
                row_rec.flags.append(Flag(
                    "before-rate-table", "warning",
                    f"{date} is before {cst.EARLIEST_DATE}, the first date with tabulated rates"))
            else:
                bd = cst.calculate(amount, province, date, tax_included=inclusive)
                net = bd.subtotal
                for t in bd.taxes:
                    expected[group_of(t.code)] += t.amount
        if not verified and inclusive:
            net = amount - collected  # best guess: the Tax column is what was included
        exp_total = sum(expected.values(), ZERO)
        gross = amount if inclusive else amount + collected
        if verified:
            alloc = self._allocate(expected, collected)
            unverified = ZERO
        else:
            alloc = {g: ZERO for g in GROUPS}
            unverified = collected

        if kind == "sale" and verified and has_tax_col and province != geo.OUTSIDE:
            diff = collected - exp_total
            if diff < -self.tol:
                row_rec.flags.append(Flag(
                    "under-collected", "warning",
                    f"collected {collected} vs expected {exp_total} ({province}): {diff}"))
            elif diff > self.tol:
                row_rec.flags.append(Flag(
                    "over-collected", "warning",
                    f"collected {collected} vs expected {exp_total} ({province}): +{diff}"))
        if province == geo.OUTSIDE and collected != 0:
            row_rec.flags.append(Flag(
                "tax-on-export", "warning",
                f"{collected} of tax collected on an address outside Canada"))
        if sign < 0:
            net, gross, unverified = -net, -gross, -unverified
            expected = {g: -v for g, v in expected.items()}
            alloc = {g: -v for g, v in alloc.items()}

        period = period_key(date, self.o["period"])
        fee_amount = fee if fee is not None else ZERO
        itc = None
        if kind == "sale" and (fee or tax_on_fee):
            itc = self._itc(fee_amount, tax_on_fee, date)
            if itc is None and fee_amount != 0:
                self.itc_missing_rows += 1
        sale = Entry(period, date, province, kind, gross, net, expected, alloc,
                     unverified, fee=fee_amount if kind == "sale" else ZERO, itc=itc)
        self.entries.append(sale)

        # refunds of a payment or invoice row, booked in the period of the refund
        if kind == "sale":
            if refunded is None or refunded == 0:
                if status == "refunded" and export_type != "balance":
                    refunded = amount
                    row_rec.flags.append(Flag(
                        "refund-assumed-full", "warning",
                        "status is Refunded but Amount Refunded is empty; assumed a full refund"))
            if refunded is not None and refunded > 0:
                if amount > 0:
                    ratio = refunded / amount
                else:
                    ratio = Decimal(0)
                if ratio > 1:
                    ratio = Decimal(1)
                    row_rec.flags.append(Flag(
                        "refund-exceeds-amount", "warning",
                        "Amount Refunded is larger than Amount; capped at a full refund"))
                rdate = date
                if cell("refund_date"):
                    try:
                        rdate = parse_date(cell("refund_date"), self.tz)
                    except ValueError:
                        row_rec.flags.append(Flag(
                            "refund-date-missing", "warning",
                            "the refund date is unreadable; refund booked in the sale's period"))
                else:
                    row_rec.flags.append(Flag(
                        "refund-date-missing", "warning",
                        "no refund date in the export; refund booked in the sale's period"))
                self.entries.append(self._scale(sale, ratio, rdate))

        if row_rec.flags:
            self.flagged.append(row_rec)


def build_report(
    sources: Any,
    *,
    export_type: str = "auto",
    period: str = "month",
    default_province: str | None = None,
    merchant_province: str | None = None,
    tax_included: bool = True,
    fees_include_tax: bool = True,
    timezone: str = "UTC",
    minor_units: bool = False,
    tolerance: str | Decimal = "0.02",
) -> Report:
    """Build a report from one or more Stripe CSV exports (paths or text streams)."""
    if isinstance(sources, (str, Path)) or hasattr(sources, "read"):
        sources = [sources]
    if not sources:
        raise ValueError("no input files")
    if period not in ("month", "quarter"):
        raise ValueError("period must be 'month' or 'quarter'")
    if export_type != "auto" and export_type not in columns.EXPORT_TYPES:
        raise ValueError(f"export type must be auto, {', '.join(columns.EXPORT_TYPES)}")

    def province(value: str | None, label: str) -> str | None:
        if value is None:
            return None
        code = geo.province_from_text(value)
        if code is None:
            raise ValueError(f"{label}: '{value}' is not a Canadian province or territory")
        return code

    if timezone.upper() == "UTC":
        tz: dt.tzinfo = dt.timezone.utc
    else:
        import zoneinfo
        try:
            tz = zoneinfo.ZoneInfo(timezone)
        except Exception:
            raise ValueError(f"unknown time zone '{timezone}'") from None

    opts = {
        "period": period,
        "default_province": province(default_province, "default province"),
        "merchant_province": province(merchant_province, "merchant province"),
        "tax_included": bool(tax_included),
        "fees_include_tax": bool(fees_include_tax),
        "timezone": timezone,
        "minor_units": bool(minor_units),
        "tolerance": str(tolerance),
        "export_type": export_type,
    }
    b = _Builder(opts, tz)
    files: list[str] = []
    types_seen: list[str] = []
    for src in sources:
        name, headers, rows = _read_source(src)
        files.append(name)
        etype = export_type if export_type != "auto" else columns.detect_export_type(headers)
        types_seen.append(etype)
        colmap = columns.build_colmap(headers, etype)
        if "amount" not in colmap:
            raise ValueError(f"{name}: no Amount column for a {etype} export")
        if "created" not in colmap:
            raise ValueError(f"{name}: no date column (Created (UTC) / created / date)")
        if "tax" not in colmap:
            b.warnings.append(
                f"{name}: no Tax column, so tax collected is taken as 0 for every row "
                "and rows are not flagged as under-collected one by one.")
        if etype == "balance" and not any(k.endswith("_state") for k in colmap):
            b.warnings.append(
                f"{name}: balance transactions carry no address, so provinces come from "
                "--default-province or stay UNKNOWN.")
        if etype == "balance" and "amount_refunded" in colmap:
            b.warnings.append(f"{name}: Amount Refunded ignored in a balance export "
                              "(refunds are their own rows).")
        for i, row in enumerate(rows, start=2):
            b.process(etype, name, i, row, colmap)
    opts["export_types"] = types_seen

    if b.itc_missing_rows:
        b.warnings.append(
            f"ITC hint incomplete: {b.itc_missing_rows} row(s) with fees have neither a "
            "Taxes On Fee column nor --merchant-province.")
    if any(r.province == geo.UNKNOWN for r in b.flagged):
        n = sum(1 for r in b.flagged if r.province == geo.UNKNOWN)
        b.warnings.append(
            f"{n} row(s) have no province: their tax is shown as 'unverified' and is "
            "not compared. Use --default-province if most customers are in one province.")

    by_key: dict[tuple[str, str], Summary] = {}
    for e in b.entries:
        by_key.setdefault((e.period, e.province), Summary(e.period, e.province)).add(e)
    summary = [by_key[k] for k in sorted(by_key)]
    totals: list[Summary] = []
    grand = Summary("ALL", "ALL")
    for per in sorted({s.period for s in summary}):
        t = Summary(per, "ALL")
        for s in summary:
            if s.period == per:
                _merge(t, s)
        totals.append(t)
        _merge(grand, t)
    totals.append(grand)

    return Report(
        options=opts, summary=summary, totals=totals, flagged=b.flagged,
        excluded=b.excluded, excluded_totals=b.excluded_totals, skipped=dict(b.skipped),
        warnings=b.warnings, files=files, rows_read=b.rows_read,
        itc_complete=b.itc_missing_rows == 0,
    )


def _merge(into: Summary, s: Summary) -> None:
    into.sales += s.sales
    into.refunds += s.refunds
    for name in ("gross", "net_sales", "gst_hst_expected", "gst_hst_collected",
                 "pst_expected", "pst_collected", "qst_expected", "qst_collected",
                 "unverified_collected", "fees", "itc_hint"):
        setattr(into, name, getattr(into, name) + getattr(s, name))
