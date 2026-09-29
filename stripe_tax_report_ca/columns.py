"""Column-name variants of the Stripe Dashboard CSV exports and export-type detection."""
from __future__ import annotations

import re

EXPORT_TYPES = ("payments", "balance", "invoices")


def norm_header(name: str) -> str:
    """'Created (UTC)' -> 'createdutc', 'customer_email' -> 'customeremail'."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lstrip("\ufeff").lower())


# field -> accepted normalized headers, in order of preference
FIELDS: dict[str, list[str]] = {
    "id": ["id", "paymentid", "chargeid", "invoiceid", "transactionid", "balancetransactionid"],
    "created": [
        "createddateutc", "createdutc", "created", "createddate", "datecreated",
        "createdat", "transactiondate", "date",
    ],
    "amount_refunded": ["amountrefunded", "refundedamount"],
    "refund_date": [
        "refundeddateutc", "refundeddate", "refundedatutc", "refundedat",
        "refunddateutc", "refunddate",
    ],
    "fee": ["fee", "fees", "stripefee", "stripefees"],
    "taxes_on_fee": ["taxesonfee", "taxonfee", "feetax"],
    "tax": ["tax", "taxamount", "totaltax", "taxes"],
    "currency": ["currency"],
    "status": ["status"],
    "captured": ["captured"],
    "email": ["customeremail", "email", "billingemail", "receiptemail"],
    "type": ["type", "reportingcategory"],
    "description": ["description"],
}

# The amount column depends on the export type.
AMOUNT_ALIASES: dict[str, list[str]] = {
    "payments": ["amount", "grossamount", "gross"],
    "balance": ["amount", "gross", "grossamount"],
    "invoices": ["total", "amountpaid", "amountdue", "amount"],
}

# Location: (state aliases, country aliases), in order of preference:
# shipping address, customer address, card / billing address.
LOCATION_GROUPS: list[tuple[str, list[str], list[str]]] = [
    (
        "shipping",
        ["shippingaddressstate", "shippingstate", "shippingaddressprovince", "shipstate"],
        ["shippingaddresscountry", "shippingcountry"],
    ),
    (
        "customer",
        ["customeraddressstate", "customerstate", "customeraddressprovince"],
        ["customeraddresscountry", "customercountry"],
    ),
    (
        "billing",
        [
            "cardaddressstate", "billingaddressstate", "billingdetailsaddressstate",
            "billingstate", "cardaddressprovince", "billingaddressprovince",
            "state", "province",
        ],
        [
            "cardaddresscountry", "billingaddresscountry", "billingdetailsaddresscountry",
            "billingcountry", "country",
        ],
    ),
]

_INVOICE_MARKERS = {
    "amountdue", "amountpaid", "amountremaining", "subtotal", "hostedinvoiceurl",
    "invoicepdf", "invoiceid", "number",
}


def detect_export_type(headers: list[str]) -> str:
    """Return 'payments', 'balance' or 'invoices' from the header row."""
    h = {norm_header(x) for x in headers}
    if "net" in h and ({"type", "reportingcategory"} & h):
        return "balance"
    if h & _INVOICE_MARKERS:
        return "invoices"
    if h & {"amount", "amountrefunded", "gross", "grossamount"}:
        return "payments"
    raise ValueError(
        "cannot tell what kind of Stripe export this is: no Amount column found "
        "(expected a payments, balance transactions or invoices export)"
    )


def build_colmap(headers: list[str], export_type: str) -> dict[str, str]:
    """Map each logical field to the first matching real header of this file."""
    by_norm: dict[str, str] = {}
    for header in headers:
        by_norm.setdefault(norm_header(header), header)
    fields = dict(FIELDS)
    fields["amount"] = AMOUNT_ALIASES[export_type]
    colmap: dict[str, str] = {}
    for field, aliases in fields.items():
        for alias in aliases:
            if alias in by_norm:
                colmap[field] = by_norm[alias]
                break
    for label, states, countries in LOCATION_GROUPS:
        for kind, aliases in (("state", states), ("country", countries)):
            for alias in aliases:
                if alias in by_norm:
                    colmap[f"{label}_{kind}"] = by_norm[alias]
                    break
    return colmap
