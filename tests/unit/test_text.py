import pytest

from erp_migration.etl.transform import description_key
from erp_migration.parsing.text import (
    name_key,
    normalize_channel,
    normalize_doc,
    normalize_employee_code,
    normalize_movement_type,
    normalize_sku,
)


@pytest.mark.parametrize(
    "raw",
    [
        "FAB-COT-001",
        "fab-cot-001",
        "FAB COT 001",
        "FABCOT001",
        "FAB-COT-1",
        "FAB/COT/001",
        " FAB-COT-001 ",
    ],
)
def test_sku_variants(raw):
    assert normalize_sku(raw) == "FAB-COT-001"


@pytest.mark.parametrize("raw", ["", None, "Assorted offcuts", "FAB-CO-001", "FAB-COT-0001"])
def test_unreadable_sku(raw):
    assert normalize_sku(raw) is None


@pytest.mark.parametrize(
    "raw", ["PO-2019-00042", "po 2019/42", "PO2019-00042", "PO/2019/042", "Po-2019-42"]
)
def test_document_numbers(raw):
    assert normalize_doc(raw, "purchases") == "PO-2019-00042"


def test_document_prefix_must_fit_the_domain():
    assert normalize_doc("RCT-2019-00042", "purchases") is None
    assert normalize_doc("Total March 2019", "purchases") is None
    assert normalize_doc("grn 2019/7", "inventory") == "GRN-2019-00007"


@pytest.mark.parametrize("raw", ["E014", "e14", "EMP-014", "E-014", "14", 14])
def test_employee_codes(raw):
    assert normalize_employee_code(raw) == "E014"


def test_name_key_ignores_order_case_and_punctuation():
    assert name_key("SMITH John") == name_key("Smith, John") == name_key("john smith")
    assert name_key("José Núñez") == "jose nunez"


def test_description_key_matches_the_sql_expression():
    # core.products.description_key = btrim(regexp_replace(lower(d), '[^a-z0-9]+', ' ', 'g'))
    assert description_key("Cotton poplin, white, 150cm") == "cotton poplin white 150cm"
    assert description_key("COTTON POPLIN - WHITE - 150CM") == "cotton poplin white 150cm"
    assert description_key("  ") is None


def test_vocabularies():
    assert normalize_channel("W/sale") == "wholesale"
    assert normalize_channel("Shop") == "showroom"
    assert normalize_channel("Online") is None
    assert normalize_movement_type("Issued to production") == "issue"
    assert normalize_movement_type("Opening balance") == "adjustment"
    assert normalize_movement_type("XFER") is None
