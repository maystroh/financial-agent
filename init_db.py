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
from openai import OpenAI

load_dotenv()

MODEL = os.environ.get("MODEL", "google/gemini-3-flash-preview")


def get_ai_client() -> OpenAI:
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
    )


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


# matches D.MM or DD.MM prefix in DATE LIBELLE
_DATE_PREFIX_RE = re.compile(r"^(\d{1,2})\.(\d{2})")


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


CATEGORIES = {"groceries", "rent", "transport", "income", "cash",
              "utilities", "entertainment", "health", "transfers", "other"}

_CATEGORIZE_PROMPT = """\
Categorize each transaction into exactly one category from this list:
groceries, rent, transport, income, cash, utilities, entertainment, health, transfers, other

Return a JSON array where each element is {{"id": <id>, "category": "<category>"}}.
Do not include any other text.

Transactions:
{lines}"""


def categorize_batch(client: OpenAI, txs: list[dict]) -> dict[int, str]:
    """Send a batch of transactions to Claude for categorization.

    Args:
        client: OpenAI client
        txs: list of dicts with keys: id, description, debit, credit

    Returns:
        dict mapping id → category string
    """
    lines = []
    for tx in txs:
        if tx["debit"] is not None:
            amount = f"debit:{tx['debit']}"
        elif tx["credit"] is not None:
            amount = f"credit:{tx['credit']}"
        else:
            amount = "amount:unknown"
        lines.append(f"{tx['id']}|{tx['description']}|{amount}")

    prompt = _CATEGORIZE_PROMPT.format(lines="\n".join(lines))
    response = client.chat.completions.create(
        model=MODEL,
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.choices[0].message.content.strip()
    # Strip markdown code fences that some models add despite being asked not to
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        items = json.loads(raw)
        return {
            item["id"]: item["category"] if item["category"] in CATEGORIES else "other"
            for item in items
        }
    except (json.JSONDecodeError, KeyError, TypeError):
        print(f"[warn] categorize_batch: failed to parse response (first 200 chars): {raw[:200]!r}")
        return {tx["id"]: "other" for tx in txs}


def load_csv(csv_path: str) -> list[dict]:
    """Read a comptes_full.csv and return list of normalized transaction dicts (no category yet)."""
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    # Normalize column names (strip whitespace)
    df.columns = [c.strip() for c in df.columns]
    rows = df.to_dict("records")
    collapsed = collapse_rows(rows)
    result = []
    for tx in collapsed:
        transaction_date = parse_transaction_date(
            tx["description"], tx["valeur"])
        value_date = datetime.strptime(
            tx["valeur"], "%d.%m.%y").strftime("%Y-%m-%d")
        result.append({
            "transaction_date": transaction_date,
            "value_date": value_date,
            "description": tx["description"],
            "debit": normalize_amount(tx["debit_raw"]),
            "credit": normalize_amount(tx["credit_raw"]),
            "category": "other",  # filled in later by categorize step
            "source_file": csv_path,
            "source_row_index": tx["source_row_index"],
        })
    return result


CSV_SOURCES = [
    "../../bankStatements/2014_2016_current_account_output/comptes_full.csv",
    "../../bankStatements/2016_2025_current_account_output/comptes_full.csv",
]
BATCH_SIZE = 200
DB_PATH = "financial.db"


def main() -> None:
    client = get_ai_client()
    conn = create_db(DB_PATH)

    for csv_path in CSV_SOURCES:
        if not Path(csv_path).exists():
            print(f"[skip] {csv_path} not found")
            continue
        print(f"[load] {csv_path}")
        transactions = load_csv(csv_path)
        print(f"  {len(transactions)} transactions parsed")

        # Assign IDs for batching (temporary, 1-based within this run)
        for i, tx in enumerate(transactions):
            tx["_tmp_id"] = i + 1

        # Categorize in batches
        categories: dict[int, str] = {}
        for batch_start in range(0, len(transactions), BATCH_SIZE):
            batch = transactions[batch_start: batch_start + BATCH_SIZE]
            batch_input = [
                {"id": tx["_tmp_id"], "description": tx["description"],
                 "debit": tx["debit"], "credit": tx["credit"]}
                for tx in batch
            ]
            print(
                f"  categorizing batch {batch_start // BATCH_SIZE + 1} ({len(batch)} rows)...")
            result = categorize_batch(client, batch_input)
            categories.update(result)

        for tx in transactions:
            tx["category"] = categories.get(tx["_tmp_id"], "other")
            del tx["_tmp_id"]

        insert_transactions(conn, transactions)
        print(
            f"  done ({conn.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]} total rows in DB)")

    conn.close()
    print("[done] financial.db ready")


if __name__ == "__main__":
    main()
