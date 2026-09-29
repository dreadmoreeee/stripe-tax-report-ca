"""Command line: stripe-tax-report-ca FILE... [options]."""
from __future__ import annotations

import argparse
import sys

from . import __version__
from .engine import build_report
from .render import FORMATS, render


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stripe-tax-report-ca",
        description="Canadian sales tax report (GST/HST/PST/QST) from Stripe Dashboard CSV "
                    "exports. Offline; not tax advice.",
    )
    p.add_argument("files", nargs="+", metavar="FILE", help="Stripe CSV export(s); '-' reads stdin")
    p.add_argument("--type", dest="export_type", default="auto",
                   choices=["auto", "payments", "balance", "invoices"],
                   help="export type (default: detect from the headers)")
    p.add_argument("--period", choices=["month", "quarter"], default="month")
    p.add_argument("--format", choices=list(FORMATS), default="markdown")
    p.add_argument("-o", "--output", help="write the report to this file instead of stdout")
    p.add_argument("--default-province", metavar="XX",
                   help="province for sales with no province (code or name); "
                        "without it those sales are flagged UNKNOWN")
    p.add_argument("--merchant-province", metavar="XX",
                   help="your province, used to estimate the GST/HST in Stripe fees (ITC hint)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--tax-included", dest="tax_included", action="store_true", default=True,
                      help="Amount is what the customer paid, tax included (default)")
    mode.add_argument("--tax-excluded", dest="tax_included", action="store_false",
                      help="Amount is the price before tax")
    p.add_argument("--fees-exclude-tax", dest="fees_include_tax", action="store_false",
                   help="the Fee column does not include tax (default: it does)")
    p.add_argument("--tz", default="UTC", metavar="ZONE",
                   help="time zone that decides each sale's date, e.g. America/Moncton "
                        "(default: UTC, as exported)")
    p.add_argument("--minor-units", action="store_true",
                   help="amounts are in cents (API-style exports)")
    p.add_argument("--tolerance", default="0.02", metavar="CAD",
                   help="ignore per-row differences up to this (default 0.02)")
    p.add_argument("--fail-on-under-collected", action="store_true",
                   help="exit 1 if any sale is flagged under-collected (for CI)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
    sources = [sys.stdin if f == "-" else f for f in args.files]
    try:
        report = build_report(
            sources, export_type=args.export_type, period=args.period,
            default_province=args.default_province, merchant_province=args.merchant_province,
            tax_included=args.tax_included, fees_include_tax=args.fees_include_tax,
            timezone=args.tz, minor_units=args.minor_units, tolerance=args.tolerance)
        text = render(report, args.format)
    except (OSError, ValueError) as exc:
        print(f"stripe-tax-report-ca: error: {exc}", file=sys.stderr)
        return 2
    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
        except OSError as exc:
            print(f"stripe-tax-report-ca: error: {exc}", file=sys.stderr)
            return 2
    else:
        sys.stdout.write(text)
    if args.fail_on_under_collected and report.under_collected:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
