"""Every reason a source row can fail to load. Seeded into etl.reject_reasons."""

from __future__ import annotations

# code: (category, stage, description)
#   category: noise (layout rows, skipped) | data (rejected) | duplicate (rejected)
#   stage:    extract (layout) | parse (Python) | load (SQL)
REASONS: dict[str, tuple[str, str, str]] = {
    "BLANK_ROW": ("noise", "extract", "Empty row inside the table"),
    "SUBTOTAL_ROW": ("noise", "extract", "Subtotal or grand-total line typed into the table"),
    "REPEATED_HEADER": ("noise", "extract", "Header row pasted again further down the sheet"),
    "MISSING_FIELD": ("data", "parse", "A required field is blank"),
    "BAD_CODE": ("data", "parse", "Document number or product code cannot be read"),
    "BAD_DATE": ("data", "parse", "Date cannot be read or does not exist (e.g. 31/02)"),
    "AMBIGUOUS_DATE": (
        "data",
        "parse",
        "Day and month could be either way round and nothing settles it",
    ),
    "DATE_OUT_OF_RANGE": ("data", "parse", "Date falls outside the book's period"),
    "BAD_PERIOD": ("data", "parse", "Payroll month cannot be read from the sheet name"),
    "BAD_AMOUNT": ("data", "parse", "Amount is not a number or is not positive"),
    "BAD_QUANTITY": ("data", "parse", "Quantity is zero, negative or not a number"),
    "UNKNOWN_CURRENCY": ("data", "parse", "No currency in the row, the amount or the header"),
    "CURRENCY_CONFLICT": ("data", "parse", "Currency column and amount text disagree"),
    "AMOUNT_MISMATCH": ("data", "parse", "Amount is not quantity times unit price"),
    "NET_MISMATCH": ("data", "parse", "Net pay is not gross minus deductions"),
    "UNKNOWN_MOVEMENT_TYPE": ("data", "parse", "Stock movement type is not recognised"),
    "UNKNOWN_CHANNEL": ("data", "parse", "Sales channel is not recognised"),
    "UNKNOWN_PRODUCT": ("data", "load", "Product code or description is not in the item master"),
    "UNKNOWN_EMPLOYEE": ("data", "load", "No employee number and the name matches no employee"),
    "AMBIGUOUS_EMPLOYEE": ("data", "load", "No employee number and the name matches several"),
    "PO_HEADER_MISMATCH": (
        "data",
        "load",
        "Line disagrees with its purchase order on supplier, date or currency",
    ),
    "FX_RATE_MISSING": ("data", "load", "No exchange rate for the transaction month"),
    "CONFLICTING_DUPLICATE": (
        "duplicate",
        "load",
        "Same document line keyed twice with different values",
    ),
}
