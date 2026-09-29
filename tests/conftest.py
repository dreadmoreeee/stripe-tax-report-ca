import csv
import sys
from pathlib import Path

import pytest

TOOL_DIR = Path(__file__).resolve().parent.parent
for path in (TOOL_DIR, Path(__file__).resolve().parent):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

try:
    import canada_sales_tax  # noqa: F401
except ImportError:  # library checked out next to the tools, not installed
    for checkout in (TOOL_DIR.parent / "canada-sales-tax", TOOL_DIR.parent.parent / "canada-sales-tax"):
        if (checkout / "canada_sales_tax").is_dir():
            sys.path.insert(0, str(checkout))
            break

PAYMENT_HEADERS = [
    "id", "Created date (UTC)", "Amount", "Amount Refunded", "Currency", "Status", "Fee",
    "Tax", "Refunded date (UTC)", "Customer Email", "Card Address State",
    "Card Address Country",
]


@pytest.fixture
def csv_file(tmp_path):
    """csv_file(headers, rows, name='x.csv') -> path of a synthetic CSV export."""
    def make(headers, rows, name="export.csv"):
        path = tmp_path / name
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(headers)
            w.writerows(rows)
        return path
    return make


@pytest.fixture
def payments(csv_file):
    """payments(rows_of_dicts) -> path; missing keys become blank cells."""
    def make(rows, headers=None, name="payments.csv"):
        headers = headers or PAYMENT_HEADERS
        return csv_file(headers, [[r.get(h, "") for h in headers] for r in rows], name)
    return make


def sale(id="py_1", date="2026-02-10 15:00:00", amount="115.00", tax="15.00", state="NB",
         country="CA", **extra):
    row = {
        "id": id, "Created date (UTC)": date, "Amount": amount, "Currency": "CAD",
        "Status": "Paid", "Tax": tax, "Card Address State": state,
        "Card Address Country": country, "Customer Email": "client@example.com",
        "Amount Refunded": "0.00",
    }
    row.update(extra)
    return row
