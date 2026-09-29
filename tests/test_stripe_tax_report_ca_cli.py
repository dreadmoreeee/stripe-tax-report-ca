import csv
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import sale

from stripe_tax_report_ca import build_report
from stripe_tax_report_ca.cli import main

TOOL_DIR = Path(__file__).resolve().parent.parent
EXAMPLE = TOOL_DIR / "examples" / "stripe-payments-2026.csv"


def test_markdown_by_default(payments, capsys):
    assert main([str(payments([sale()]))]) == 0
    out = capsys.readouterr().out
    assert "# Stripe sales tax report" in out and "Not tax advice" in out


@pytest.mark.parametrize("fmt", ["markdown", "csv", "json"])
def test_each_format_has_the_notice(payments, capsys, fmt):
    assert main([str(payments([sale()])), "--format", fmt]) == 0
    assert "Not tax advice" in capsys.readouterr().out


def test_output_file(payments, tmp_path):
    out = tmp_path / "report.json"
    assert main([str(payments([sale()])), "--format", "json", "-o", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["rows_read"] == 1


def test_options_are_passed_through(payments, capsys):
    rows = [sale("a", state="", country="", amount="100.00", tax="15.00")]
    code = main([str(payments(rows)), "--format", "json", "--period", "quarter",
                 "--default-province", "New Brunswick", "--tax-excluded",
                 "--merchant-province", "NB", "--tz", "America/Moncton"])
    data = json.loads(capsys.readouterr().out)
    assert code == 0
    assert data["options"]["period"] == "quarter" and data["options"]["tax_included"] is False
    assert data["options"]["default_province"] == "NB"
    assert data["summary"][0]["period"] == "2026-Q1"


def test_missing_file_exit_2(capsys):
    assert main(["no-such-file.csv"]) == 2
    assert "error" in capsys.readouterr().err


def test_bad_province_exit_2(payments, capsys):
    assert main([str(payments([sale()])), "--default-province", "Atlantis"]) == 2
    assert "default province" in capsys.readouterr().err


def test_unrecognized_export_exit_2(csv_file):
    assert main([str(csv_file(["foo", "bar"], [["1", "2"]]))]) == 2


def test_fail_on_under_collected(payments):
    ok = payments([sale()], name="ok.csv")
    bad = payments([sale(tax="0.00")], name="bad.csv")
    assert main([str(ok), "--fail-on-under-collected"]) == 0
    assert main([str(bad), "--fail-on-under-collected"]) == 1
    assert main([str(bad)]) == 0


def test_reads_stdin(payments, monkeypatch, capsys):
    text = payments([sale()]).read_text(encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    assert main(["-", "--format", "csv"]) == 0
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert rows[0]["record"] == "summary"


def test_python_dash_m_runs_the_example():
    proc = subprocess.run(
        [sys.executable, "-m", "stripe_tax_report_ca", str(EXAMPLE), "--period", "quarter",
         "--merchant-province", "NB"],
        cwd=TOOL_DIR, capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    assert "Not tax advice" in proc.stdout and "2026-Q2" in proc.stdout


def test_example_export_findings():
    report = build_report(EXAMPLE, period="quarter", merchant_province="NB")
    assert report.rows_read == 22
    assert len(report.excluded) == 1 and report.excluded[0].currency == "USD"
    ids = {r.id for r in report.under_collected}
    assert ids == {"py_3Example0007", "py_3Example0010", "py_3Example0020"}
    assert report.totals[-1].refunds == 2


def test_console_script_target_exists():
    import tomllib
    data = tomllib.loads((TOOL_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["scripts"]["stripe-tax-report-ca"] == "stripe_tax_report_ca.cli:main"
    assert data["project"]["requires-python"] == ">=3.10"


def test_sources_are_ascii_only():
    files = list((TOOL_DIR / "stripe_tax_report_ca").glob("*.py"))
    files += list((TOOL_DIR / "tests").glob("*.py"))
    for path in files:
        path.read_bytes().decode("ascii")
