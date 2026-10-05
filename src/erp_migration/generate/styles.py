"""How each year's books were kept.

Different clerks kept the books in different years, so each workbook gets a
style: layout, header wording, date format, amount format, where the currency
lives, how sheets were split. The tables below are fixed (not random) so every
kind of mess is guaranteed to appear in a ten-year run.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BookStyle:
    layout: str = "plain"  # plain | title | two_level
    header: int = 0  # index into the domain's header variants
    date: str = "dmy"  # excel_date | excel_date_strays | serial | dmy | mdy | iso | text_month
    #                    | dmy_dash | mixed_clerks
    amount: str = "number"  # number | commas | slash_equals | spaces
    currency: str = "column"  # column | column_partial | header | embedded
    sheets: str = "single"  # single | quarterly | halves | monthly | by_category
    codes: str = "clean"  # clean | messy (SKUs, document and employee numbers)
    case: str = "as_is"  # as_is | upper (supplier names and descriptions)
    has_sku: bool = True
    has_unit_price: bool = True
    qty_text: bool = False  # quantities typed as text with units ("120 m")
    notes_sheet: bool = False
    repeated_header: bool = False
    stock_card: bool = True  # inventory: Qty In / Qty Out columns instead of a signed Qty
    names: str = "given_surname"  # payroll: given_surname | SURNAME_given | surname_comma
    sheet_names: str = "Mon YYYY"  # payroll sheet naming
    summary_sheet: bool = False


PURCHASE_STYLES = [
    BookStyle(
        layout="title",
        date="dmy",
        amount="slash_equals",
        currency="header",
        has_sku=False,
        has_unit_price=False,
    ),
    BookStyle(
        layout="title",
        date="dmy",
        amount="commas",
        currency="header",
        has_sku=False,
        has_unit_price=False,
        case="upper",
        notes_sheet=True,
    ),
    BookStyle(layout="two_level", date="excel_date", sheets="quarterly"),
    BookStyle(
        layout="two_level",
        date="serial",
        currency="column_partial",
        sheets="quarterly",
        codes="messy",
    ),
    BookStyle(layout="plain", header=2, date="mdy", amount="commas", codes="messy"),
    BookStyle(
        layout="plain",
        header=2,
        date="iso",
        amount="commas",
        currency="embedded",
        sheets="quarterly",
        notes_sheet=True,
    ),
    BookStyle(layout="title", header=1, date="text_month", repeated_header=True, qty_text=True),
    BookStyle(layout="title", date="mixed_clerks", amount="commas", codes="messy"),
    BookStyle(
        layout="two_level",
        date="dmy_dash",
        amount="spaces",
        currency="column_partial",
        sheets="halves",
    ),
    BookStyle(layout="plain", header=1, date="excel_date_strays", sheets="quarterly"),
]
# A second copy of one year's Q4, re-keyed later by someone else.
REVISED_PURCHASE_STYLE = BookStyle(
    layout="title", header=1, date="dmy", amount="commas", currency="embedded", codes="messy"
)

SALES_STYLES = [
    BookStyle(
        layout="title", date="dmy", amount="slash_equals", currency="header", sheets="monthly"
    ),
    BookStyle(
        layout="title",
        date="dmy",
        amount="commas",
        currency="embedded",
        sheets="monthly",
        case="upper",
    ),
    BookStyle(layout="plain", header=1, date="excel_date"),
    BookStyle(layout="plain", header=1, date="serial", sheets="quarterly", codes="messy"),
    BookStyle(layout="two_level", date="mdy", amount="commas", sheets="quarterly"),
    BookStyle(
        layout="plain",
        header=2,
        date="iso",
        amount="commas",
        currency="column_partial",
        sheets="monthly",
    ),
    BookStyle(layout="title", header=1, date="text_month", repeated_header=True),
    BookStyle(layout="title", date="dmy", amount="spaces", sheets="halves", codes="messy"),
    BookStyle(layout="plain", header=2, date="excel_date", sheets="monthly", notes_sheet=True),
    BookStyle(layout="two_level", date="iso", currency="embedded", sheets="quarterly"),
]

INVENTORY_STYLES = [
    BookStyle(layout="title", date="dmy"),
    BookStyle(layout="title", date="dmy", sheets="by_category", codes="messy"),
    BookStyle(layout="plain", header=0, date="excel_date", stock_card=False),
    BookStyle(layout="plain", header=1, date="serial", sheets="by_category", stock_card=False),
    BookStyle(layout="two_level", date="mdy"),
    BookStyle(
        layout="plain",
        header=1,
        date="iso",
        sheets="by_category",
        stock_card=False,
        notes_sheet=True,
    ),
    BookStyle(layout="title", header=1, date="text_month", repeated_header=True),
    BookStyle(
        layout="title",
        header=0,
        date="dmy_dash",
        sheets="by_category",
        stock_card=False,
        codes="messy",
    ),
    BookStyle(layout="plain", date="excel_date", qty_text=True),
    BookStyle(layout="plain", header=1, date="iso", sheets="by_category", stock_card=False),
]

PAYROLL_STYLES = [
    BookStyle(layout="title", amount="number", sheet_names="Mon YYYY"),
    BookStyle(layout="title", amount="commas", names="SURNAME_given", sheet_names="MON-YY"),
    BookStyle(
        layout="plain", header=1, names="surname_comma", sheet_names="YYYY-MM", summary_sheet=True
    ),
    BookStyle(layout="two_level", amount="slash_equals", sheet_names="Month"),
    BookStyle(
        layout="plain", header=1, amount="spaces", names="SURNAME_given", sheet_names="MM.YYYY"
    ),
    BookStyle(layout="title", codes="messy", sheet_names="Mon YYYY"),
    BookStyle(
        layout="plain", header=1, amount="commas", names="surname_comma", sheet_names="MonYY"
    ),
    BookStyle(layout="two_level", sheet_names="Month", summary_sheet=True),
    BookStyle(
        layout="plain",
        amount="slash_equals",
        names="SURNAME_given",
        codes="messy",
        sheet_names="YYYY-MM",
    ),
    BookStyle(layout="title", header=1, sheet_names="Mon YYYY"),
]


def style_for(styles: list[BookStyle], year: int, start_year: int) -> BookStyle:
    return styles[(year - start_year) % len(styles)]


# Header wording per domain. Each variant is a list of (field, label) in column order.
HEADERS: dict[str, list[list[tuple[str, str]]]] = {
    "purchases": [
        [
            ("date", "Date"),
            ("po_number", "PO No"),
            ("supplier", "Supplier"),
            ("sku", "Item Code"),
            ("description", "Description"),
            ("quantity", "Qty"),
            ("unit_price", "Unit Price"),
            ("amount", "Amount"),
            ("currency", "Currency"),
        ],
        [
            ("date", "Invoice Date"),
            ("po_number", "Order No."),
            ("supplier", "Vendor"),
            ("sku", "Code"),
            ("description", "Item Description"),
            ("quantity", "Quantity"),
            ("unit_price", "Rate"),
            ("amount", "Total"),
            ("currency", "Curr"),
        ],
        [
            ("po_number", "PO #"),
            ("date", "Date of purchase"),
            ("supplier", "Supplier Name"),
            ("sku", "Product Code"),
            ("description", "Item"),
            ("quantity", "Qty"),
            ("unit_price", "U/Price"),
            ("amount", "Value"),
            ("currency", "Ccy"),
        ],
    ],
    "sales": [
        [
            ("date", "Date"),
            ("receipt_no", "Receipt No"),
            ("channel", "Channel"),
            ("sku", "Item Code"),
            ("description", "Description"),
            ("quantity", "Qty"),
            ("unit_price", "Unit Price"),
            ("amount", "Amount"),
            ("currency", "Currency"),
        ],
        [
            ("date", "Sale Date"),
            ("receipt_no", "Invoice No."),
            ("channel", "Sales Channel"),
            ("sku", "Code"),
            ("description", "Item"),
            ("quantity", "Quantity"),
            ("unit_price", "Price"),
            ("amount", "Total"),
            ("currency", "Curr"),
        ],
        [
            ("receipt_no", "Receipt #"),
            ("date", "Date"),
            ("channel", "Outlet"),
            ("sku", "Product Code"),
            ("description", "Product"),
            ("quantity", "Qty"),
            ("unit_price", "U/Price"),
            ("amount", "Value"),
            ("currency", "Ccy"),
        ],
    ],
    "inventory_card": [
        [
            ("date", "Date"),
            ("voucher_no", "Voucher No"),
            ("sku", "Item Code"),
            ("description", "Description"),
            ("movement_type", "Movement"),
            ("qty_in", "Qty In"),
            ("qty_out", "Qty Out"),
            ("reference", "Reference"),
        ],
        [
            ("date", "Date"),
            ("voucher_no", "Doc No."),
            ("sku", "Code"),
            ("description", "Item"),
            ("movement_type", "Type"),
            ("qty_in", "Received"),
            ("qty_out", "Issued"),
            ("reference", "Ref"),
        ],
    ],
    "inventory_signed": [
        [
            ("date", "Date"),
            ("voucher_no", "Ref No"),
            ("sku", "Code"),
            ("description", "Item"),
            ("movement_type", "Type"),
            ("quantity", "Quantity"),
            ("reference", "Remarks"),
        ],
        [
            ("date", "Movement Date"),
            ("voucher_no", "Voucher"),
            ("sku", "Product Code"),
            ("description", "Product"),
            ("movement_type", "Movement Type"),
            ("quantity", "Qty"),
            ("reference", "Reference"),
        ],
    ],
    "payroll": [
        [
            ("employee_code", "Emp No"),
            ("name", "Employee Name"),
            ("position", "Position"),
            ("gross", "Gross Salary"),
            ("deductions", "Deductions"),
            ("net", "Net Pay"),
        ],
        [
            ("employee_code", "Staff ID"),
            ("name", "Name"),
            ("position", "Job Title"),
            ("gross", "Gross"),
            ("deductions", "Total Deductions"),
            ("net", "Net"),
        ],
    ],
}

# Two-level headers: (parent label or None, [(field, child label)]).
GROUPED_HEADERS: dict[str, list[tuple[str | None, list[tuple[str, str]]]]] = {
    "purchases": [
        ("Order", [("date", "Date"), ("po_number", "Number")]),
        (None, [("supplier", "Supplier")]),
        ("Item", [("sku", "Code"), ("description", "Description")]),
        ("Quantity and price", [("quantity", "Qty"), ("unit_price", "Unit price")]),
        ("Amount", [("amount", "Value"), ("currency", "Currency")]),
    ],
    "sales": [
        ("Sale", [("date", "Date"), ("receipt_no", "Number"), ("channel", "Channel")]),
        ("Item", [("sku", "Code"), ("description", "Description")]),
        ("Quantity and price", [("quantity", "Qty"), ("unit_price", "Unit price")]),
        ("Amount", [("amount", "Value"), ("currency", "Currency")]),
    ],
    "inventory_card": [
        ("Document", [("date", "Date"), ("voucher_no", "Number")]),
        ("Item", [("sku", "Code"), ("description", "Description")]),
        (None, [("movement_type", "Movement")]),
        ("Quantity", [("qty_in", "In"), ("qty_out", "Out")]),
        (None, [("reference", "Reference")]),
    ],
    "payroll": [
        ("Employee", [("employee_code", "ID"), ("name", "Name"), ("position", "Position")]),
        ("Pay (Frw)", [("gross", "Gross"), ("deductions", "Deductions"), ("net", "Net")]),
    ],
}

TITLES = {
    "purchases": "PURCHASES BOOK",
    "sales": "SALES BOOK",
    "inventory": "STOCK MOVEMENTS",
    "payroll": "PAYROLL",
}

CURRENCY_LABELS = {"RWF": ["RWF", "Rwf", "FRW", "Frw", "RF"], "USD": ["USD", "usd", "US$", "$"]}
CURRENCY_TOKENS = {
    "RWF": ["RWF {x}", "{x} Frw", "Frw {x}", "{x} RWF", "RF {x}"],
    "USD": ["USD {x}", "${x}", "US$ {x}", "{x} USD"],
}
CHANNEL_LABELS = {
    "showroom": ["Showroom", "SHOWROOM", "Shop", "Retail"],
    "wholesale": ["Wholesale", "W/sale", "WHOLESALE"],
    "export": ["Export", "EXP", "Export order"],
}
MOVEMENT_LABELS = {
    "receipt": ["GRN", "Receipt", "Received", "Goods received"],
    "issue": ["Issue", "Issued to production", "ISS", "Issue - production"],
    "production": ["Production", "FG in", "Production output", "PROD"],
    "sale": ["Sale", "Sales", "Sales issue", "SALES"],
    "adjustment": ["Adjustment", "Adj", "Stock count adj"],
}
