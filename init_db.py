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


_DATE_PREFIX_RE = re.compile(r"^(\d{1,2})\.(\d{2})")


def parse_transaction_date(date_libelle: str, valeur: str) -> str:
    """Parse transaction date from DATE LIBELLE prefix + year from VALEUR."""
    val_dt = datetime.strptime(valeur, "%d.%m.%y")
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
