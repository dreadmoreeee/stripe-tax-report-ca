# stripe-tax-report-ca

Build a Canadian sales tax report (GST/HST, PST, QST) from **Stripe Dashboard CSV exports**. For each sale it works out the tax that should have been collected on the sale date, compares it with the Tax column, and rolls the result up by month or quarter and by province. It shows refunds, Stripe fees and an ITC hint, and flags every row that needs a human look. No API keys, no network calls. The only dependency is [canada-sales-tax](https://github.com/dreadmoreeee/canada-sales-tax) for the rates. Python 3.10+.

**Not tax advice.** Expected amounts come from published rates and the assumptions below. Your bookkeeper or accountant decides what to file.

## Quick start

```bash
pip install .            # installs the stripe-tax-report-ca console script
python -m stripe_tax_report_ca payments.csv --period quarter --merchant-province NB
python -m stripe_tax_report_ca payments.csv invoices.csv --format csv -o report.csv
python -m stripe_tax_report_ca payments.csv --default-province NB --tz America/Moncton
```

Run it in place with `python -m stripe_tax_report_ca` from this folder. Output is Markdown by default; `--format csv` and `--format json` are for spreadsheets and scripts. Every format carries the notice "Not tax advice".

## What it reads

Export from the Stripe Dashboard (Payments, Balance, or Billing > Invoices > Export). The type is detected from the headers, or set it with `--type payments|balance|invoices`. Header matching ignores case, spaces and punctuation, so `Created (UTC)`, `created` and `CREATED_UTC` are the same column. Several files can be given; a repeated `id` is counted once.

| What | Accepted headers (first one present wins) |
|---|---|
| id | `id`, `Payment ID`, `Charge ID`, `Invoice ID`, `Balance Transaction ID` |
| date | `Created date (UTC)`, `Created (UTC)`, `created`, `Created date`, `date` |
| amount | payments and balance: `Amount`, `Gross`; invoices: `Total`, `Amount Paid`, `Amount Due`, `Amount` |
| amount refunded | `Amount Refunded`, `Refunded Amount` |
| refund date | `Refunded date (UTC)`, `Refunded date`, `Refund Date` |
| fee | `Fee`, `Fees`, `Stripe Fee` |
| taxes on fee | `Taxes On Fee` (optional; used for the ITC hint when present) |
| tax collected | `Tax`, `Tax Amount`, `Total Tax`, `Taxes` |
| currency | `Currency` |
| status | `Status` |
| captured | `Captured` (`false` rows are skipped) |
| balance row type | `Type`, `Reporting Category` |
| email | `Customer Email`, `customer_email`, `Email`, `Billing Email` (accepted, not used in the report) |
| state, in this order | `Shipping Address State`, `Customer Address State`, then `Card Address State` / `Billing Address State` / `State` / `Province` |
| country, same order | `Shipping Address Country`, `Customer Address Country`, then `Card Address Country` / `Billing Address Country` / `Country` |

Amounts are in dollars (`1,234.50`, `$10`, `(5.00)` all parse); use `--minor-units` for exports in cents. Dates are ISO (`2026-02-10 15:00:00`, with or without `T`/`Z`), a plain date, or epoch seconds. Stripe exports UTC; `--tz America/Moncton` decides each sale's date in your zone instead, which matters for sales just before or after midnight at a period boundary.

Which rows count:

- **payments**: `Paid`, `Succeeded`, `Refunded`, `Partially refunded` or a blank status. Failed, canceled and uncaptured rows are skipped and counted.
- **invoices**: `paid` only.
- **balance**: `charge`/`payment` rows are sales, `refund`/`payment_refund` rows are refunds, everything else (payouts, adjustments, Stripe billing fees) is skipped and counted. Balance exports carry no address, so use `--default-province`.

Rows with an unreadable amount or date are left out of every total and listed under "Excluded".

## How each sale is treated

- **Province.** The first address group with data decides: shipping, then customer, then card/billing. State text is matched as a code or an English or French name (`NB`, `New Brunswick`, `Nouveau-Brunswick`, `CA-NB`, `PEI`). A country that is not Canada wins over a state that looks like a province (`NL` in Mexico). No province: `--default-province` if given (the row is flagged `province-defaulted`), otherwise the sale is `UNKNOWN`, its tax is shown as *unverified* and it is not compared.
- **Outside Canada.** Flagged `outside-canada` with a **hint only** that exports are often zero-rated. Expected tax is 0; tax collected on such a row is flagged `tax-on-export`.
- **Expected tax.** `canada_sales_tax.calculate(amount, province, on=sale_date, tax_included=...)`, so a rate change is applied from its effective date. Each tax is rounded to the cent per sale, as Stripe does. Sales before 2019-07-01 have no tabulated rates and are flagged `before-rate-table`.
- **Tax collected.** The `Tax` column. A blank cell counts as 0 and is flagged `tax-blank`. With no Tax column at all, collected is 0 for every row and the report warns once instead of flagging every row.
- **Tax-included or excluded.** `--tax-included` (default) treats Amount as what the customer paid, which is how Stripe reports charges. `--tax-excluded` treats Amount as the price before tax. This changes the expected tax a lot, so check it against a sale you know. Net sales is the amount before tax.
- **Under and over-collected.** A row is flagged `under-collected` or `over-collected` when collected minus expected is more than `--tolerance` (default 0.02). If the price was tax-included, the expected tax is what is owed on what the customer paid, even if less was collected.
- **One Tax figure, several taxes.** The export has a single Tax number, so for BC, MB, SK and QC it is split between GST/HST and PST/QST in proportion to the expected tax of that sale. A shortfall therefore shows up in both columns. The totals across all taxes are exact; the split is an allocation.
- **Refunds.** A refund reduces net sales, expected tax and collected tax in the same proportion as the refunded amount (`Amount Refunded / Amount`), using the rates of the original sale. **Period rule:** the reduction is booked in the period of the refund date (`Refunded date (UTC)`), not the sale's period. With no refund date the refund falls in the sale's period and the row is flagged `refund-date-missing`. Status `Refunded` with no amount is taken as a full refund (flagged); a refund larger than the amount is capped (flagged). Stripe keeps its fee on a refund, so fees are not reduced. In balance exports each refund row stands alone and is computed at the refund date.
- **Currency.** CAD only. A row in another currency is flagged `currency-not-cad`, left out of every total and listed in its own table with its amounts, fees and refunds per currency. The `Converted Amount` columns are not used. A blank currency is taken as CAD and flagged.
- **Fees and the ITC hint.** Stripe fees are totalled per period. The GST/HST inside them is estimated as an input tax credit **hint**: from the `Taxes On Fee` column when the export has it, otherwise from the GST/HST rate of `--merchant-province` on the sale date, treating the Fee column as tax-included (`--fees-exclude-tax` if it is not). Only GST and HST are counted, not PST or QST. Without either source the hint shows `n/a`. This is only a hint: **the bookkeeper must check the Stripe tax invoice** for the real amount.

## Output

A summary per period (`--period month|quarter`) and province, then a total row per period and a grand total:

- sales and refund counts, net sales;
- GST/HST collected, expected and the difference (collected minus expected; negative is under-collected);
- PST and QST collected, expected and difference, in their own table;
- unverified tax (collected on rows with no province);
- Stripe fees and the ITC hint;
- flagged rows with their flags, excluded rows, warnings and skipped-row counts.

CSV is one table with a `record` column (`summary`, `total`, `flagged`, `excluded`) and a `notice` column on every row. JSON holds the same data plus the options used.

## CLI

```
stripe-tax-report-ca FILE... [--type auto|payments|balance|invoices] [--period month|quarter]
    [--format markdown|csv|json] [-o FILE] [--default-province XX] [--merchant-province XX]
    [--tax-included | --tax-excluded] [--fees-exclude-tax] [--tz ZONE] [--minor-units]
    [--tolerance CAD] [--fail-on-under-collected]
```

`FILE` may be `-` for stdin. Exit codes: `0` ok, `1` a sale is under-collected and `--fail-on-under-collected` was given, `2` bad arguments or input. [examples/ci-example.yml](examples/ci-example.yml) is a CI job sketch (kept out of any workflow folder on purpose).

## Python API

```python
from stripe_tax_report_ca import build_report, render

report = build_report("payments.csv", period="quarter", merchant_province="NB")
report.totals[-1].gst_hst_difference      # Decimal; negative = under-collected
report.under_collected                    # rows flagged under-collected
report.flagged, report.excluded, report.warnings
print(render(report, "markdown"))         # or "csv", "json"
```

More in [examples/api_example.py](examples/api_example.py).

## Limits

- CAD only, one Tax figure per row, and no per-line detail: a sale that mixes taxable and zero-rated items cannot be told apart.
- Place of supply is guessed from the address in the export. Some supplies (digital services, exports, real property) follow other rules.
- QST and PST rules such as small-supplier registration are not modelled; rates come from the library.
- The ITC hint ignores QST recovery and any fee the export does not show.

## Tests

```bash
python -m pytest -q -p no:cacheprovider --import-mode=importlib stripe-tax-report-ca
```

Synthetic CSV fixtures only, no network. They cover header variants, province mapping (codes and names), the default and unknown province, full and partial refunds, USD flagging, fees and the ITC hint, tax-included mode, and each output format.

## Measured result

The tool run on [examples/stripe-payments-2026.csv](examples/stripe-payments-2026.csv), a synthetic export of 22 payments in 2026 (customers at example.com, provinces NB, ON, QC, BC, AB, NS, SK and MB, one full refund, one partial refund, one USD charge, one sale with no address, one CAD sale to a US address, a Fee column), from this folder:

```
python -m stripe_tax_report_ca examples/stripe-payments-2026.csv --period quarter --merchant-province NB
```

```
# Stripe sales tax report (Canada)

> Not tax advice. This report is a bookkeeping aid: expected amounts come from published rates and may not match your real obligations. Have your bookkeeper or accountant check it before you file.

Files: examples/stripe-payments-2026.csv | Rows read: 22 | Period: quarter | Amounts: tax-included | Currency: CAD only

## Warnings

- 1 row(s) have no province: their tax is shown as 'unverified' and is not compared. Use --default-province if most customers are in one province.

## GST/HST by period and province

| Period | Province | Sales | Refunds | Net sales | GST/HST collected | GST/HST expected | Difference | Unverified |
|---|---|---|---|---|---|---|---|---|
| 2026-Q1 | AB | 1 | 0 | 500.00 | 25.00 | 25.00 | 0.00 | 0.00 |
| 2026-Q1 | BC | 1 | 0 | 180.00 | 9.00 | 9.00 | 0.00 | 0.00 |
| 2026-Q1 | NB | 2 | 0 | 370.00 | 55.50 | 55.50 | 0.00 | 0.00 |
| 2026-Q1 | NS | 1 | 0 | 750.00 | 105.00 | 105.00 | 0.00 | 0.00 |
| 2026-Q1 | ON | 2 | 0 | 484.07 | 52.00 | 62.93 | -10.93 | 0.00 |
| 2026-Q1 | QC | 2 | 0 | 460.00 | 23.00 | 23.00 | 0.00 | 0.00 |
| **2026-Q1** | **ALL** | **9** | **0** | **2744.07** | **269.50** | **280.43** | **-10.93** | **0.00** |
| 2026-Q2 | AB | 1 | 0 | 500.00 | 25.00 | 25.00 | 0.00 | 0.00 |
| 2026-Q2 | BC | 1 | 0 | 468.75 | 10.42 | 23.44 | -13.02 | 0.00 |
| 2026-Q2 | NB | 2 | 1 | 120.00 | 18.00 | 18.00 | 0.00 | 0.00 |
| 2026-Q2 | ON | 1 | 1 | 44.60 | 5.80 | 5.80 | 0.00 | 0.00 |
| 2026-Q2 | SK | 1 | 0 | 300.00 | 15.00 | 15.00 | 0.00 | 0.00 |
| 2026-Q2 | UNKNOWN | 1 | 0 | 150.00 | 0.00 | 0.00 | 0.00 | 22.50 |
| **2026-Q2** | **ALL** | **7** | **2** | **1583.35** | **74.22** | **87.24** | **-13.02** | **22.50** |
| 2026-Q3 | MB | 1 | 0 | 750.00 | 37.50 | 37.50 | 0.00 | 0.00 |
| 2026-Q3 | NB | 1 | 0 | 491.30 | 65.00 | 73.70 | -8.70 | 0.00 |
| 2026-Q3 | NS | 1 | 0 | 80.00 | 11.20 | 11.20 | 0.00 | 0.00 |
| 2026-Q3 | ON | 1 | 0 | 400.00 | 52.00 | 52.00 | 0.00 | 0.00 |
| 2026-Q3 | OUTSIDE-CA | 1 | 0 | 95.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| **2026-Q3** | **ALL** | **5** | **0** | **1816.30** | **165.70** | **174.40** | **-8.70** | **0.00** |
| **ALL** | **ALL** | **21** | **2** | **6143.72** | **509.42** | **542.07** | **-32.65** | **22.50** |

Difference = collected minus expected (negative = under-collected). Refunds reduce sales and tax in the period of the refund. Unverified = tax collected on rows whose province is unknown.

## PST / QST (shown separately)

| Period | Province | PST collected | PST expected | PST diff. | QST collected | QST expected | QST diff. |
|---|---|---|---|---|---|---|---|
| 2026-Q1 | BC | 12.60 | 12.60 | 0.00 | 0.00 | 0.00 | 0.00 |
| 2026-Q1 | QC | 0.00 | 0.00 | 0.00 | 45.89 | 45.89 | 0.00 |
| 2026-Q2 | BC | 14.58 | 32.81 | -18.23 | 0.00 | 0.00 | 0.00 |
| 2026-Q2 | SK | 18.00 | 18.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| 2026-Q3 | MB | 52.50 | 52.50 | 0.00 | 0.00 | 0.00 | 0.00 |

The Stripe Tax column is one figure, so it is split between GST/HST and PST/QST in proportion to the expected tax of each sale.

## Fees and ITC hint

| Period | Stripe fees | ITC hint (GST/HST in fees) |
|---|---|---|
| 2026-Q1 | 92.13 | 12.02 |
| 2026-Q2 | 93.86 | 12.24 |
| 2026-Q3 | 60.76 | 7.93 |
| ALL | 246.75 | 32.18 |

HINT ONLY: the input tax credit (ITC) figure is an estimate of the GST/HST in Stripe's fees. The bookkeeper must check the Stripe tax invoice for the real amount.

## Flagged rows

| File | Line | ID | Date | Province | Amount | Flags |
|---|---|---|---|---|---|---|
| examples/stripe-payments-2026.csv | 8 | py_3Example0007 | 2026-03-05 | ON | 95.00 | under-collected: collected 0.00 vs expected 10.93 (ON): -10.93 |
| examples/stripe-payments-2026.csv | 11 | py_3Example0010 | 2026-04-02 | BC | 525.00 | under-collected: collected 25.00 vs expected 56.25 (BC): -31.25 |
| examples/stripe-payments-2026.csv | 17 | py_3Example0016 | 2026-06-04 | UNKNOWN | 172.50 | province-unknown: no province in the row and no default: expected tax not computed |
| examples/stripe-payments-2026.csv | 19 | py_3Example0018 | 2026-07-07 | OUTSIDE-CA | 95.00 | outside-canada: US: HINT ONLY: this address is outside Canada. Some exports are zero-rated (0% GST/HST), but it depends on what you sold; confirm the rule for this supply. |
| examples/stripe-payments-2026.csv | 21 | py_3Example0020 | 2026-08-13 | NB | 565.00 | under-collected: collected 65.00 vs expected 73.70 (NB): -8.70 |

## Excluded from the totals

| File | Line | ID | Date | Currency | Amount | Fee | Reason |
|---|---|---|---|---|---|---|---|
| examples/stripe-payments-2026.csv | 16 | py_3Example0015 | 2026-05-29 | USD | 900.00 | 35.40 | currency-not-cad: USD is not CAD: left out of every total (this tool is CAD only; report it separately using CAD values) |

| Currency | Rows | Amount | Fees | Refunded |
|---|---|---|---|---|
| USD | 1 | 900.00 | 35.40 | 0.00 |

---
Not tax advice. This report is a bookkeeping aid: expected amounts come from published rates and may not match your real obligations. Have your bookkeeper or accountant check it before you file.
```

What it shows, honestly:

- **21 CAD sales, 1 USD row excluded.** GST/HST collected 509.42 against 542.07 expected: 32.65 short. BC also shows 18.23 short in PST. Across all taxes that is 50.88 under-collected on three sales.
- **Three sales are flagged under-collected.** The Ontario sale on 2026-03-05 (line 8) has no tax at all: 10.93 short. The BC sale on 2026-04-02 (line 11) collected only GST, 25.00 against 56.25: 31.25 short. The New Brunswick sale on 2026-08-13 (line 21) was charged at 13% instead of 15%: 65.00 against 73.70, 8.70 short. These are the mistakes seeded in the fixture; the tool flagged exactly these three as under-collected.
- **Read the size of those numbers with the tax-included assumption in mind.** The fixture's amounts are tax-inclusive by design (525.00 is 500.00 plus 5% GST). If the price was really 525.00 tax-included, 56.25 is owed. If those customers were quoted 500.00 plus tax, the business under-charged the customer and the shortfall is real money either way, but the fix differs. Check with `--tax-excluded` on your own data.
- **The BC split is an allocation.** The single Tax figure (25.00) is divided between GST and PST in proportion to what BC expects, so 13.02 of GST/HST and 18.23 of PST are reported short. The tax total is exact; only the split is an estimate.
- **Refunds.** The full refund of the 2026-04-22 sale is booked in Q2 (refunded 2026-05-06), so New Brunswick Q2 nets to the other sale only. The partial refund (40.00 of 90.40) removes 35.40 of net sales and 4.60 of tax in Q2, in proportion.
- **One sale has no province** (line 17): its 22.50 of tax is shown as unverified, not compared. With `--default-province NB` it would be checked as a New Brunswick sale. The CAD sale to a Washington address is flagged with the export hint only; whether it is zero-rated is not decided here.
- **Fees:** 246.75 in Stripe fees on the 21 CAD rows, with an ITC hint of 32.18 assuming a New Brunswick merchant and tax-included fees. The 35.40 of fees on the USD row is listed apart. Check the Stripe tax invoice for the real figure.

pytest: `133 passed in 0.77s`

## Author

Marvin Palencia, founder of [DeMark Studio](https://demarkstudio.ca), Miramichi, New Brunswick, Canada. Portfolio: [marvin.demarkstudio.ca](https://marvin.demarkstudio.ca)

MIT License.
