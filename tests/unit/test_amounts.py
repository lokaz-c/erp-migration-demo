from decimal import Decimal

import pytest

from erp_migration.parsing.amounts import (
    ParseError,
    normalize_currency,
    parse_money,
    parse_quantity,
    resolve_currency,
    to_decimal,
)


@pytest.mark.parametrize(
    ("value", "amount", "currency"),
    [
        (125000, "125000", None),
        (1250.5, "1250.5", None),
        ("125,000", "125000", None),
        ("125 000", "125000", None),
        ("125 000", "125000", None),
        ("125,000/=", "125000", None),
        ("125,000/-", "125000", None),
        ("RWF 125,000", "125000", "RWF"),
        ("125,000 Frw", "125000", "RWF"),
        ("RF 125,000", "125000", "RWF"),
        ("$1,250.50", "1250.50", "USD"),
        ("US$ 1,250.50", "1250.50", "USD"),
        ("1,250.50 USD", "1250.50", "USD"),
        ("usd 0.35", "0.35", "USD"),
        ("1.250.000", "1250000", None),
        ("1.250,50", "1250.50", None),
        ("12,5", "12.5", None),
        ("(1,250)", "-1250", None),
        ("-80", "-80", None),
    ],
)
def test_parse_money(value, amount, currency):
    money = parse_money(value)
    assert money.amount == Decimal(amount)
    assert money.currency == currency


@pytest.mark.parametrize("value", ["TBC", "n/a", "#VALUE!", "see invoice", "12,34,56", "1.2.3"])
def test_unreadable_amounts(value):
    with pytest.raises(ParseError) as err:
        parse_money(value)
    assert err.value.reason == "BAD_AMOUNT"


def test_blank_amount_is_a_missing_field():
    with pytest.raises(ParseError) as err:
        parse_money("  ")
    assert err.value.reason == "MISSING_FIELD"


def test_two_currencies_in_one_cell_conflict():
    with pytest.raises(ParseError) as err:
        parse_money("USD 1,000 RWF")
    assert err.value.reason == "CURRENCY_CONFLICT"


def test_decimal_separator_rule():
    assert to_decimal("1,250") == Decimal("1250")  # groups of three: thousands
    assert to_decimal("1,25") == Decimal("1.25")  # one or two digits: decimal comma
    assert to_decimal("1.250") == Decimal("1.250")  # a lone dot is a decimal point


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (85, "85"),
        (170.5, "170.5"),
        ("120 m", "120"),
        ("1,100 pcs", "1100"),
        ("50 pc", "50"),
        ("-85", "-85"),
        ("3 rolls", "3"),
    ],
)
def test_parse_quantity(value, expected):
    assert parse_quantity(value) == Decimal(expected)


def test_unreadable_quantity():
    with pytest.raises(ParseError) as err:
        parse_quantity("a lot")
    assert err.value.reason == "BAD_QUANTITY"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("RWF", "RWF"),
        ("Frw", "RWF"),
        ("FRW", "RWF"),
        ("RF", "RWF"),
        ("usd", "USD"),
        ("US$", "USD"),
        ("$", "USD"),
        (None, None),
        ("", None),
    ],
)
def test_normalize_currency(value, expected):
    assert normalize_currency(value) == expected


def test_unknown_currency_label():
    with pytest.raises(ParseError) as err:
        normalize_currency("EUR")
    assert err.value.reason == "UNKNOWN_CURRENCY"


def test_currency_precedence():
    assert resolve_currency("Frw", [None, None], "USD") == ("RWF", "column")
    assert resolve_currency(None, ["USD", None], "RWF") == ("USD", "embedded")
    assert resolve_currency(None, [None, None], "RWF") == ("RWF", "header")


def test_currency_conflicts_and_gaps():
    with pytest.raises(ParseError) as err:
        resolve_currency("RWF", ["USD", None], None)
    assert err.value.reason == "CURRENCY_CONFLICT"
    with pytest.raises(ParseError) as err:
        resolve_currency(None, ["USD", "RWF"], None)
    assert err.value.reason == "CURRENCY_CONFLICT"
    with pytest.raises(ParseError) as err:
        resolve_currency(None, [None, None], None)
    assert err.value.reason == "UNKNOWN_CURRENCY"
