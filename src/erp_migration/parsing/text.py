"""Normalizers for codes, names and small vocabularies."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

DOC_PREFIXES = {
    "purchases": {"PO"},
    "sales": {"RCT"},
    "inventory": {"GRN", "ISS", "PRD", "SIV", "ADJ"},
}
_DOC = re.compile(r"([A-Z]{2,3})\W*(\d{4})\W*(\d{1,5})")
_SKU = re.compile(r"([A-Z]{3})([A-Z]{3})(\d{1,3})")
_EMPLOYEE = re.compile(r"(?:EMP|E)?\W*(\d{1,4})")

CHANNELS = {
    "showroom": "showroom",
    "shop": "showroom",
    "retail": "showroom",
    "wholesale": "wholesale",
    "w/sale": "wholesale",
    "w sale": "wholesale",
    "export": "export",
    "exp": "export",
    "export order": "export",
}
MOVEMENT_TYPES = {
    "grn": "receipt",
    "receipt": "receipt",
    "received": "receipt",
    "goods received": "receipt",
    "issue": "issue",
    "issued to production": "issue",
    "iss": "issue",
    "issue - production": "issue",
    "production": "production",
    "fg in": "production",
    "production output": "production",
    "prod": "production",
    "sale": "sale",
    "sales": "sale",
    "sales issue": "sale",
    "adjustment": "adjustment",
    "adj": "adjustment",
    "stock count adj": "adjustment",
    "opening balance": "adjustment",
}


def clean(value: Any) -> str:
    """Cell value as trimmed text with single spaces; '' for blanks."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_sku(value: Any) -> str | None:
    """'fab-cot-001', 'FAB COT 001', 'FABCOT001', 'FAB-COT-1' -> 'FAB-COT-001'."""
    s = re.sub(r"[^A-Za-z0-9]", "", clean(value)).upper()
    m = _SKU.fullmatch(s)
    return f"{m[1]}-{m[2]}-{int(m[3]):03d}" if m else None


def normalize_doc(value: Any, domain: str) -> str | None:
    """'po 2019/42', 'PO2019-00042', 'Po-2019-42' -> 'PO-2019-00042'."""
    m = _DOC.fullmatch(clean(value).upper())
    if not m or m[1] not in DOC_PREFIXES[domain]:
        return None
    return f"{m[1]}-{m[2]}-{int(m[3]):05d}"


def normalize_po_reference(value: Any) -> str | None:
    """A goods receipt's reference, when it points at a purchase order."""
    return normalize_doc(value, "purchases")


def normalize_employee_code(value: Any) -> str | None:
    """'E014', 'e14', 'EMP-014', 'E-014', 14 -> 'E014'."""
    m = _EMPLOYEE.fullmatch(clean(value).upper())
    return f"E{int(m[1]):03d}" if m else None


def name_key(value: Any) -> str:
    """Order-insensitive key: 'SMITH John', 'Smith, John', 'john smith' -> 'john smith'."""
    s = unicodedata.normalize("NFKD", clean(value)).encode("ascii", "ignore").decode()
    tokens = re.sub(r"[^a-z\s]", " ", s.casefold()).split()
    return " ".join(sorted(tokens))


def normalize_channel(value: Any) -> str | None:
    return CHANNELS.get(clean(value).casefold())


def normalize_movement_type(value: Any) -> str | None:
    return MOVEMENT_TYPES.get(clean(value).casefold())
