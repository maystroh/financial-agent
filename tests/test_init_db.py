# tests/test_init_db.py
import pytest
from init_db import collapse_rows, parse_transaction_date, normalize_amount

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


import sqlite3, tempfile, os
from init_db import create_db

def test_create_db_creates_transactions_table(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = create_db(db_path)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    assert "transactions" in tables
    assert "conversations" in tables
    assert "messages" in tables
    conn.close()

def test_create_db_transactions_columns(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = create_db(db_path)
    cursor = conn.execute("PRAGMA table_info(transactions)")
    cols = {row[1] for row in cursor.fetchall()}
    assert cols == {"id", "transaction_date", "value_date", "description",
                    "debit", "credit", "category", "source_file", "source_row_index"}
    conn.close()

def test_create_db_messages_cascade(tmp_path):
    """Deleting a conversation must cascade-delete its messages."""
    db_path = str(tmp_path / "test.db")
    conn = create_db(db_path)
    conn.execute("INSERT INTO conversations (title, created_at) VALUES ('test', '2024-01-01')")
    conn.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (1, 'user', 'hi', '2024-01-01')")
    conn.commit()
    conn.execute("DELETE FROM conversations WHERE id = 1")
    conn.commit()
    cursor = conn.execute("SELECT COUNT(*) FROM messages")
    assert cursor.fetchone()[0] == 0
    conn.close()

def test_create_db_idempotent(tmp_path):
    """Calling create_db twice does not raise."""
    db_path = str(tmp_path / "test.db")
    create_db(db_path).close()
    create_db(db_path).close()


from init_db import insert_transactions

def _make_tx(idx=0, date="2016-04-04", valeur="2016-04-04",
             desc="CB LECLERC", debit=3.45, credit=None,
             category="groceries", src="test.csv"):
    return {
        "transaction_date": date,
        "value_date": valeur,
        "description": desc,
        "debit": debit,
        "credit": credit,
        "category": category,
        "source_file": src,
        "source_row_index": idx,
    }

def test_insert_transactions_basic(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = create_db(db_path)
    txs = [_make_tx(0), _make_tx(1, desc="CB SFR", debit=5.0)]
    insert_transactions(conn, txs)
    count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    assert count == 2

def test_insert_transactions_dedup(tmp_path):
    """Same source_file + source_row_index → silently ignored on second insert."""
    db_path = str(tmp_path / "test.db")
    conn = create_db(db_path)
    tx = _make_tx(0)
    insert_transactions(conn, [tx])
    insert_transactions(conn, [tx])  # second time
    count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    assert count == 1

def test_insert_transactions_values(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = create_db(db_path)
    tx = _make_tx(0, date="2020-06-15", desc="CB LECLERC", debit=10.5, credit=None, category="groceries")
    insert_transactions(conn, [tx])
    row = conn.execute("SELECT transaction_date, description, debit, credit, category FROM transactions").fetchone()
    assert row == ("2020-06-15", "CB LECLERC", 10.5, None, "groceries")


import json
from unittest.mock import MagicMock
from init_db import categorize_batch

VALID_CATEGORIES = {"groceries","rent","transport","income","cash",
                    "utilities","entertainment","health","transfers","other"}

def test_categorize_batch_returns_mapping():
    """categorize_batch(client, txs) → {id: category} for each tx."""
    txs = [
        {"id": 1, "description": "CB LECLERC 01/04/16", "debit": 3.45, "credit": None},
        {"id": 2, "description": "PRLV SEPA LUXIOR IMMOBILIER Appel de loyer", "debit": 270.0, "credit": None},
    ]
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps([
        {"id": 1, "category": "groceries"},
        {"id": 2, "category": "rent"},
    ])
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response

    result = categorize_batch(mock_client, txs)
    assert result == {1: "groceries", 2: "rent"}

def test_categorize_batch_falls_back_on_bad_json():
    """If Claude returns invalid JSON, all entries in batch → 'other'."""
    txs = [{"id": 1, "description": "UNKNOWN", "debit": 5.0, "credit": None}]
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "sorry I can't do that"
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response

    result = categorize_batch(mock_client, txs)
    assert result == {1: "other"}

def test_categorize_batch_handles_both_none_amount():
    """Transaction with debit=None and credit=None should not crash."""
    txs = [{"id": 1, "description": "CREDIT", "debit": None, "credit": None}]
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps([{"id": 1, "category": "other"}])
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response
    result = categorize_batch(mock_client, txs)
    assert result == {1: "other"}

def test_categorize_batch_clamps_unknown_category():
    """If Claude returns an unknown category, clamp to 'other'."""
    txs = [{"id": 1, "description": "CB TEST", "debit": 1.0, "credit": None}]
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps([{"id": 1, "category": "shopping"}])
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response

    result = categorize_batch(mock_client, txs)
    assert result[1] == "other"


import io
from init_db import load_csv

SAMPLE_CSV = """DATE LIBELLE,BLANK1,BLANK2,VALEUR,DEBIT,CREDIT
04.04 CB LECLERC 01/04/16,,,04.04.16,"3,45",
05.04 PRLV SEPA LUXIOR IMMOBILIER,,,05.04.16,"270,00",
LIBELLE:Appel de loyer du mois,,,,,
28.04 VIREMENT SALAIRE,,,28.04.16,,"2000,00"
"""

def test_load_csv_returns_collapsed_transactions(tmp_path):
    csv_file = tmp_path / "test.csv"
    csv_file.write_text(SAMPLE_CSV, encoding="utf-8")
    result = load_csv(str(csv_file))
    # 3 anchor rows (LUXIOR continuation is merged)
    assert len(result) == 3

def test_load_csv_parses_dates(tmp_path):
    csv_file = tmp_path / "test.csv"
    csv_file.write_text(SAMPLE_CSV, encoding="utf-8")
    result = load_csv(str(csv_file))
    assert result[0]["transaction_date"] == "2016-04-04"
    assert result[0]["value_date"] == "2016-04-04"

def test_load_csv_normalizes_amounts(tmp_path):
    csv_file = tmp_path / "test.csv"
    csv_file.write_text(SAMPLE_CSV, encoding="utf-8")
    result = load_csv(str(csv_file))
    assert result[0]["debit"] == 3.45
    assert result[0]["credit"] is None
    assert result[2]["credit"] == 2000.0
    assert result[2]["debit"] is None

def test_load_csv_stores_source_file(tmp_path):
    csv_file = tmp_path / "test.csv"
    csv_file.write_text(SAMPLE_CSV, encoding="utf-8")
    result = load_csv(str(csv_file))
    assert all(r["source_file"] == str(csv_file) for r in result)
