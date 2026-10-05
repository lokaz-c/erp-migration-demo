from datetime import date, datetime

import pytest

from erp_migration.parsing.dates import (
    DAY_FIRST,
    MIXED,
    MONTH_FIRST,
    NO_EVIDENCE,
    DateRange,
    convention,
    parse_period,
    resolve_column,
    tokenize,
)

YEAR_2019 = DateRange.for_year(2019)


@pytest.mark.parametrize(
    ("value", "expected", "method"),
    [
        (datetime(2019, 3, 5, 0, 0), date(2019, 3, 5), "excel_date"),
        (date(2019, 3, 5), date(2019, 3, 5), "excel_date"),
        (43529, date(2019, 3, 5), "excel_serial"),
        ("43529", date(2019, 3, 5), "excel_serial"),
        ("2019-03-05", date(2019, 3, 5), "iso"),
        ("2019/3/5", date(2019, 3, 5), "iso"),
        ("5 Mar 2019", date(2019, 3, 5), "text_month"),
        ("05-Mar-19", date(2019, 3, 5), "text_month"),
        ("March 5, 2019", date(2019, 3, 5), "text_month"),
        ("5-MARCH-2019", date(2019, 3, 5), "text_month"),
        ("25/03/2019", date(2019, 3, 25), "day_first"),
        ("03/25/2019", date(2019, 3, 25), "month_first"),
        ("25.03.2019", date(2019, 3, 25), "day_first"),
        ("25/03/19", date(2019, 3, 25), "day_first"),
        ("07/07/2019", date(2019, 7, 7), "same_day_month"),
    ],
)
def test_unambiguous_values(value, expected, method):
    token = tokenize(value)
    assert token.value == expected
    assert token.method == method


@pytest.mark.parametrize(
    "value", ["31/02/2019", "2019-13-04", "??/03/2019", "32/13/2019", True, 12, "next week"]
)
def test_invalid_values(value):
    assert tokenize(value).method == "invalid"


def test_blank_is_missing():
    assert tokenize(None).method == "missing"
    assert tokenize("  ").method == "missing"


def test_ambiguous_value_keeps_both_readings():
    token = tokenize("05/03/2019")
    assert token.method == "ambiguous"
    assert token.day_first == date(2019, 3, 5)
    assert token.month_first == date(2019, 5, 3)


def test_convention_comes_only_from_evidence():
    assert convention([tokenize("25/03/2019"), tokenize("05/03/2019")]) == DAY_FIRST
    assert convention([tokenize("03/25/2019"), tokenize("05/03/2019")]) == MONTH_FIRST
    assert convention([tokenize("25/03/2019"), tokenize("03/25/2019")]) == MIXED
    assert convention([tokenize("05/03/2019"), tokenize("2019-01-01")]) == NO_EVIDENCE


def test_ambiguous_dates_follow_the_sheet_convention():
    day_first = [tokenize(v) for v in ["25/03/2019", "05/04/2019"]]
    assert resolve_column(day_first, YEAR_2019)[1].value == date(2019, 4, 5)
    month_first = [tokenize(v) for v in ["03/25/2019", "05/04/2019"]]
    resolved = resolve_column(month_first, YEAR_2019)[1]
    assert resolved.value == date(2019, 5, 4)
    assert resolved.method == "sheet_convention"


def test_sheet_without_evidence_uses_the_workbook_convention():
    tokens = [tokenize("05/04/2019")]
    resolved = resolve_column(tokens, YEAR_2019, book_convention=MONTH_FIRST)[0]
    assert resolved.value == date(2019, 5, 4)
    assert resolved.method == "book_convention"


def test_no_evidence_anywhere_is_rejected_not_guessed():
    resolved = resolve_column([tokenize("05/04/2019")], YEAR_2019)[0]
    assert resolved.value is None
    assert resolved.reason == "AMBIGUOUS_DATE"


def test_mixed_sheet_resolves_from_neighbouring_rows():
    # Two clerks: one day-first, one month-first. Rows are in date order.
    values = ["28/08/2019", "09/14/2019", "09/10/2019", "09/20/2019", "02/10/2019", "25/10/2019"]
    resolved = resolve_column([tokenize(v) for v in values], YEAR_2019)
    assert resolved[2].value == date(2019, 9, 10)  # between Sep 14 and Sep 20 -> month-first
    assert resolved[2].method == "neighbours"
    assert resolved[4].value == date(2019, 10, 2)  # between Sep 20 and Oct 25 -> day-first


def test_mixed_sheet_without_a_deciding_neighbour_is_rejected():
    # Mixed evidence; the only neighbour is Mar 25, and the 7-day window around it
    # (Mar 18 to Apr 1) holds neither reading of 04/05 (Apr 5 or May 4).
    values = ["25/03/2019", "03/25/2019", "04/05/2019"]
    resolved = resolve_column([tokenize(v) for v in values], YEAR_2019)
    assert resolved[2].reason == "AMBIGUOUS_DATE"


def test_dates_outside_the_book_period_are_rejected():
    resolved = resolve_column([tokenize("05/03/2091"), tokenize("2019-12-30")], YEAR_2019)
    assert resolved[0].reason == "DATE_OUT_OF_RANGE"
    assert resolved[1].value == date(2019, 12, 30)
    # Carry-over rows from the previous December are inside the slack.
    assert date(2018, 12, 29) in YEAR_2019


def test_invalid_and_missing_reasons():
    resolved = resolve_column([tokenize("31/02/2019"), tokenize(None)], YEAR_2019)
    assert [r.reason for r in resolved] == ["BAD_DATE", "MISSING_FIELD"]


@pytest.mark.parametrize(
    ("name", "year", "expected"),
    [
        ("Jan 2019", None, date(2019, 1, 1)),
        ("JAN-19", None, date(2019, 1, 1)),
        ("2019-01", None, date(2019, 1, 1)),
        ("01.2019", None, date(2019, 1, 1)),
        ("Jan19", None, date(2019, 1, 1)),
        ("January", 2019, date(2019, 1, 1)),
        ("Sept", 2019, date(2019, 9, 1)),
        ("Dec 2018 (2)", None, date(2018, 12, 1)),
        ("Summary", 2019, None),
        ("Marketing", 2019, None),
        ("January", None, None),
    ],
)
def test_payroll_periods_from_sheet_names(name, year, expected):
    assert parse_period(name, year) == expected
