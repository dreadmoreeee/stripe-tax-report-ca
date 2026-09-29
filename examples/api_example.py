"""Python API example. Run from the tool folder: python examples/api_example.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run without installing

from stripe_tax_report_ca import build_report, render

HERE = Path(__file__).resolve().parent

report = build_report(
    HERE / "stripe-payments-2026.csv",
    period="quarter",
    merchant_province="NB",     # for the ITC hint on Stripe fees
    default_province=None,      # sales with no province stay UNKNOWN
    tax_included=True,          # Amount is what the customer paid
)

total = report.totals[-1]
print(f"{total.sales} sales, net {total.net_sales:.2f} CAD")
print(f"GST/HST collected {total.gst_hst_collected:.2f}, expected {total.gst_hst_expected:.2f}, "
      f"difference {total.gst_hst_difference:+.2f}")
print(f"fees {total.fees:.2f}, ITC hint {total.itc_hint:.2f} (a hint only)")

for row in report.under_collected:
    flag = next(f for f in row.flags if f.code == "under-collected")
    print(f"under-collected: {row.id} {row.date} {flag.message}")

print(render(report, "csv").splitlines()[0])   # also: "markdown", "json"
