# tests/test_init_db.py
import pytest
from init_db import collapse_rows

def test_collapse_rows_single_anchor():
    rows = [
        {"DATE LIBELLE": "04.04 CB RETRAIT", "BLANK1": "", "BLANK2": "", "VALEUR": "04.04.16", "DEBIT": "20,00", "CREDIT": ""},
    ]
    result = collapse_rows(rows)
    assert len(result) == 1
    assert result[0]["description"] == "04.04 CB RETRAIT"
    assert result[0]["valeur"] == "04.04.16"
    assert result[0]["debit_raw"] == "20,00"
    assert result[0]["credit_raw"] == ""
    assert result[0]["source_row_index"] == 0

def test_collapse_rows_with_continuations():
    rows = [
        {"DATE LIBELLE": "05.04 PRLV SEPA LUXIOR", "BLANK1": "", "BLANK2": "", "VALEUR": "05.04.16", "DEBIT": "270,00", "CREDIT": ""},
        {"DATE LIBELLE": "LIBELLE:Appel de loyer", "BLANK1": "", "BLANK2": "", "VALEUR": "", "DEBIT": "", "CREDIT": ""},
        {"DATE LIBELLE": "REF.CLIENT:PRL-001", "BLANK1": "", "BLANK2": "", "VALEUR": "", "DEBIT": "", "CREDIT": ""},
    ]
    result = collapse_rows(rows)
    assert len(result) == 1
    assert "LIBELLE:Appel de loyer" in result[0]["description"]
    assert "REF.CLIENT:PRL-001" in result[0]["description"]
    assert result[0]["source_row_index"] == 0

def test_collapse_rows_multiple_anchors():
    rows = [
        {"DATE LIBELLE": "04.04 CB LECLERC", "BLANK1": "", "BLANK2": "", "VALEUR": "04.04.16", "DEBIT": "3,45", "CREDIT": ""},
        {"DATE LIBELLE": "05.04 VIREMENT", "BLANK1": "", "BLANK2": "", "VALEUR": "05.04.16", "DEBIT": "", "CREDIT": "150,00"},
        {"DATE LIBELLE": "INTERNET-DEPUIS LIVRET", "BLANK1": "", "BLANK2": "", "VALEUR": "", "DEBIT": "", "CREDIT": ""},
    ]
    result = collapse_rows(rows)
    assert len(result) == 2
    assert result[0]["source_row_index"] == 0
    assert result[1]["source_row_index"] == 1
    assert "INTERNET-DEPUIS LIVRET" in result[1]["description"]

def test_collapse_rows_blank1_blank2_included():
    rows = [
        {"DATE LIBELLE": "04.04 CB TEST", "BLANK1": "extra info", "BLANK2": "more info", "VALEUR": "04.04.16", "DEBIT": "5,00", "CREDIT": ""},
    ]
    result = collapse_rows(rows)
    assert "extra info" in result[0]["description"]
    assert "more info" in result[0]["description"]

def test_collapse_rows_skips_empty_anchor_without_valeur():
    """Rows with no VALEUR at start of file are skipped."""
    rows = [
        {"DATE LIBELLE": "orphan line", "BLANK1": "", "BLANK2": "", "VALEUR": "", "DEBIT": "", "CREDIT": ""},
        {"DATE LIBELLE": "04.04 CB REAL", "BLANK1": "", "BLANK2": "", "VALEUR": "04.04.16", "DEBIT": "3,00", "CREDIT": ""},
    ]
    result = collapse_rows(rows)
    assert len(result) == 1
    assert result[0]["description"] == "04.04 CB REAL"

def test_collapse_rows_continuation_blank1_blank2():
    """Continuation row's BLANK1 and BLANK2 should be appended to anchor description."""
    rows = [
        {"DATE LIBELLE": "05.04 PRLV SEPA", "BLANK1": "", "BLANK2": "", "VALEUR": "05.04.16", "DEBIT": "50,00", "CREDIT": ""},
        {"DATE LIBELLE": "REF.CLIENT:X", "BLANK1": "extra1", "BLANK2": "extra2", "VALEUR": "", "DEBIT": "", "CREDIT": ""},
    ]
    result = collapse_rows(rows)
    assert len(result) == 1
    assert "extra1" in result[0]["description"]
    assert "extra2" in result[0]["description"]

from init_db import parse_transaction_date

def test_parse_date_basic():
    assert parse_transaction_date("04.04 CB LECLERC", "04.04.16") == "2016-04-04"

def test_parse_date_single_digit_day():
    """2014-era CSVs use D.MM not DD.MM."""
    assert parse_transaction_date("5.11 CB RETRAIT", "05.11.14") == "2014-11-05"

def test_parse_date_year_boundary():
    """Dec transaction with Jan value date → year is VALEUR_year - 1."""
    assert parse_transaction_date("31.12 CB TEST", "02.01.17") == "2016-12-31"

def test_parse_date_same_month_no_adjustment():
    assert parse_transaction_date("15.06 CB TEST", "17.06.22") == "2022-06-15"

def test_parse_date_no_match_returns_valeur_date():
    """If DATE LIBELLE prefix cannot be parsed, fall back to value date."""
    assert parse_transaction_date("CREDIT", "10.03.20") == "2020-03-10"

from init_db import normalize_amount

def test_normalize_amount_basic():
    assert normalize_amount("20,00") == 20.0

def test_normalize_amount_thousands():
    assert normalize_amount("1 234,56") == 1234.56

def test_normalize_amount_empty():
    assert normalize_amount("") is None

def test_normalize_amount_dot_already():
    """Some rows may already have a period (defensive)."""
    assert normalize_amount("20.00") == 20.0

def test_normalize_amount_period_in_string():
    assert normalize_amount(".") is None
