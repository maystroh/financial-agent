# init_db.py
from __future__ import annotations
import re
import sqlite3
import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
import anthropic

load_dotenv()


def collapse_rows(rows: list[dict]) -> list[dict]:
    """Collapse multi-line CSV rows into single transaction records.

    An anchor row has a non-empty VALEUR field.
    Continuation rows (empty VALEUR) are appended to the preceding anchor's description.

    Note: expects string values in row dicts (no NaN). Use keep_default_na=False when reading CSVs.
    """
    result = []
    current: dict | None = None

    for i, row in enumerate(rows):
        valeur = str(row.get("VALEUR", "")).strip()
        if valeur:
            if current is not None:
                result.append(current)
            # Build initial description from anchor row
            parts = [str(row.get("DATE LIBELLE", "")).strip()]
            if str(row.get("BLANK1", "")).strip():
                parts.append(str(row["BLANK1"]).strip())
            if str(row.get("BLANK2", "")).strip():
                parts.append(str(row["BLANK2"]).strip())
            current = {
                "description": " ".join(p for p in parts if p),
                "valeur": valeur,
                "debit_raw": str(row.get("DEBIT", "")).strip(),
                "credit_raw": str(row.get("CREDIT", "")).strip(),
                "source_row_index": i,
            }
        else:
            if current is None:
                continue  # orphan continuation before any anchor
            parts = [str(row.get("DATE LIBELLE", "")).strip()]
            if str(row.get("BLANK1", "")).strip():
                parts.append(str(row["BLANK1"]).strip())
            if str(row.get("BLANK2", "")).strip():
                parts.append(str(row["BLANK2"]).strip())
            extra = " ".join(p for p in parts if p)
            if extra:
                current["description"] = current["description"] + " " + extra

    if current is not None:
        result.append(current)

    return result


_DATE_PREFIX_RE = re.compile(r"^(\d{1,2})\.(\d{2})")  # matches D.MM or DD.MM prefix in DATE LIBELLE


def parse_transaction_date(date_libelle: str, valeur: str) -> str:
    """Parse transaction date from DATE LIBELLE prefix + year from VALEUR.

    VALEUR format: DD.MM.YY (Python %y: 00-68 → 2000-2068, 69-99 → 1969-1999)
    Year boundary: if transaction month > value month, year = valeur_year - 1.
    Falls back to value date if prefix cannot be parsed.
    Raises ValueError if valeur is empty or not in DD.MM.YY format.
    """
    try:
        val_dt = datetime.strptime(valeur, "%d.%m.%y")
    except (ValueError, TypeError):
        raise ValueError(f"valeur must be in DD.MM.YY format, got: {valeur!r}")

    m = _DATE_PREFIX_RE.match(date_libelle.strip())
    if not m:
        return val_dt.strftime("%Y-%m-%d")
    day = int(m.group(1))
    month = int(m.group(2))
    year = val_dt.year
    if month > val_dt.month:
        year -= 1
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return val_dt.strftime("%Y-%m-%d")


def normalize_amount(raw: str) -> float | None:
    """Convert French locale amount string to float. Returns None for empty/invalid."""
    s = raw.strip().replace("\u00a0", "").replace(" ", "")
    if not s or s == ".":
        return None
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def create_db(db_path: str) -> sqlite3.Connection:
    """Create (or open) the SQLite DB and ensure schema exists. Returns open connection."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS transactions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_date TEXT NOT NULL,
            value_date       TEXT NOT NULL,
            description      TEXT NOT NULL,
            debit            REAL,
            credit           REAL,
            category         TEXT NOT NULL DEFAULT 'other',
            source_file      TEXT NOT NULL,
            source_row_index INTEGER NOT NULL,
            UNIQUE(source_file, source_row_index)
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            title      TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role            TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
            content         TEXT NOT NULL,
            created_at      TEXT NOT NULL
        );
    """)
    conn.commit()
    return conn


def insert_transactions(conn: sqlite3.Connection, transactions: list[dict]) -> None:
    """Insert transaction dicts into DB. Skips duplicates silently."""
    conn.executemany(
        """INSERT OR IGNORE INTO transactions
           (transaction_date, value_date, description, debit, credit, category, source_file, source_row_index)
           VALUES (:transaction_date, :value_date, :description, :debit, :credit, :category, :source_file, :source_row_index)""",
        transactions,
    )
    conn.commit()
