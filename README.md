# financial-agent

A two-part tool for your LCL (French bank) statements:

1. **PDF extractors** — parse transaction tables out of LCL PDFs into CSV files
2. **Financial chat app** — import those CSVs into SQLite, auto-categorize with Claude, and chat with your data through a web interface

---

## Quick Start (chat app)

```bash
# 1. Install dependencies (requires Poetry)
poetry install

# 2. Create a .env file with your Anthropic API key
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# 3. Extract PDFs to CSV (if not already done — see Extractors section below)
python bankStatements/lcl_extractor_2014.py bankStatements/pdf/2014_2016_current_account
python bankStatements/lcl_extractor.py bankStatements/pdf/2016_2025_current_account

# 4. Import CSVs into SQLite and auto-categorize transactions (~5 min, calls Claude)
poetry run python init_db.py

# 5. Launch the web app
poetry run python app.py
# → Open http://localhost:5000
```

---

## Architecture

```
PDFs  ──►  lcl_extractor*.py  ──►  comptes_full.csv
                                         │
                                    init_db.py
                                    (categorize via Claude)
                                         │
                                    financial.db (SQLite)
                                         │
                                      app.py (Flask)
                                         │
                              http://localhost:5000
```

### `init_db.py` — one-time import

Reads the two `comptes_full.csv` files, processes each row:
- **Collapses multi-line rows** (LCL splits long descriptions across multiple CSV rows)
- **Parses dates** from the `DATE LIBELLE` prefix + `VALEUR` year, handles Dec/Jan year boundary
- **Normalizes amounts** from French locale (`1 234,56` → `1234.56`)
- **Batch-categorizes** all transactions via Claude (`claude-sonnet-4-6`) in batches of 200
- **Inserts into SQLite** with deduplication (safe to run multiple times)

### `app.py` — Flask web app

- Persistent multi-conversation chat interface
- Claude uses a `query_db` tool to run read-only SQL against `financial.db`
- Responses stream token-by-token via SSE (Server-Sent Events)
- Conversation history stored in SQLite and restored on page reload

### Database schema

```sql
transactions (id, transaction_date, value_date, description, debit, credit, category, source_file, source_row_index)
conversations (id, title, created_at)
messages      (id, conversation_id, role, content, created_at)
```

Transaction categories: `groceries`, `rent`, `transport`, `income`, `cash`, `utilities`, `entertainment`, `health`, `transfers`, `other`

---

## Chat app usage

Open `http://localhost:5000` after running `python app.py`.

- Click **+ New Chat** to start a conversation
- Ask questions in plain English — Claude queries the database and answers
- Conversation titles are set automatically from your first message
- All conversations persist across page reloads

Example questions:
- *"How much did I spend on groceries in 2022?"*
- *"What were my 10 biggest expenses last year?"*
- *"Show me all rent payments since 2018"*
- *"What's my average monthly spending by category?"*
- *"Did my spending change between 2020 and 2021?"*

---

## Data

Bank statements are stored under `bankStatements/pdf/`, split by account type:

| Folder | Period | Extractor |
|--------|--------|-----------|
| `2014_2016_current_account/` | Nov 2014 – Apr 2016 | `lcl_extractor_2014.py` |
| `2016_2025_current_account/` | Apr 2016 – Sep 2025 | `lcl_extractor.py` |
| `Livret_all/` | 2018–2019 savings (Livret A) | not yet supported |

PDF filename pattern: `COMPTEDEDEPOTS_<account_id>_<YYYYMMDD>.pdf`

CSV outputs are written to:
- `bankStatements/2014_2016_current_account_output/comptes_full.csv`
- `bankStatements/2016_2025_current_account_output/comptes_full.csv`

---

## PDF Extractors

### `lcl_extractor_2014.py` — 2014–2016 statements

These PDFs use a **wide format** with explicit `DATE`, `LIBELLE`, `VALEUR`, `DEBIT`, `CREDIT` column headers.

```bash
python bankStatements/lcl_extractor_2014.py bankStatements/pdf/2014_2016_current_account
# optional custom output directory
python bankStatements/lcl_extractor_2014.py bankStatements/pdf/2014_2016_current_account --output-dir /path/to/output
```

Produces: one CSV per PDF + `comptes_full.csv` (all combined). **17 PDFs → 540 transactions.**

### `lcl_extractor.py` — 2016–2025 statements

These PDFs use the **ECRITURES format** where the first row of each table is a header row (`DATE LIBELLE / VALEUR / DEBIT / CREDIT`) and date+description are merged in a single column.

```bash
python bankStatements/lcl_extractor.py bankStatements/pdf/2016_2025_current_account
# optional custom output directory
python bankStatements/lcl_extractor.py bankStatements/pdf/2016_2025_current_account --output-dir /path/to/output
```

Produces: one CSV per PDF + `comptes_full.csv` (all combined). **109 PDFs → 5,595 transactions.**

### Extractor dependencies

- `tabula-py` — PDF table extraction (requires Java)
- `pandas`

```bash
pip install tabula-py pandas
```

---

## CSV Output Schema

Both extractors write CSVs with these 6 columns:

| Column | Description |
|--------|-------------|
| `DATE LIBELLE` | Transaction date + description (e.g. `05.11 VERSEMENT EXPRESS`) |
| `BLANK1` | Continuation column 1 (usually empty) |
| `BLANK2` | Continuation column 2 (usually empty) |
| `VALEUR` | Value date (e.g. `05.11.14`) |
| `DEBIT` | Debit amount, French locale string (e.g. `1 234,56`) |
| `CREDIT` | Credit amount, French locale string (e.g. `1 234,56`) |

Amounts are kept as French-locale strings. Convert before arithmetic:
```python
df['DEBIT'] = pd.to_numeric(df['DEBIT'].str.replace(' ', '').str.replace(',', '.'), errors='coerce').fillna(0)
```

---

## Running tests

```bash
poetry run pytest tests/ -v
# 41 tests: CSV parsing, DB schema, Flask routes, chat endpoint
```

---

## Known Limitations

### 1. Tabula column misalignment (manually corrected)
Five PDFs in `2016_2025_current_account` contain tables with an unusual 7-column layout. Tabula reads the `DEBIT`/`CREDIT` column one position off, producing rows where both amounts are `NaN` even though the values exist in the PDF. **10 rows were identified and corrected by hand** in the following output CSVs:

| File | Affected transactions |
|------|-----------------------|
| `COMPTEDEDEPOTS_05800395618_20190301.csv` | `15.02 VIREMENT XXXXXXX` × 2 |
| `COMPTEDEDEPOTS_05800395618_20190531.csv` | `03.05 CB LA POSTE L290190` × 5, `05.05 VIREMENT XXXXXXX` × 1 |
| `COMPTEDEDEPOTS_05800395618_20191202.csv` | `22.11 VIREMENT XXXXXXX` × 1 |
| `COMPTEDEDEPOTS_05800395618_20200902.csv` | `18.08 VIREMENT M. XXXXXXX` × 1 |
| `COMPTEDEDEPOTS_05800395618_20221102.csv` | `14.10 VIREMENT MLLE XXXXXXX` × 1 |

### 2. `.` placeholder in DEBIT column
LCL prints a `.` (dot) in the DEBIT column when a row has only a CREDIT amount. 29 such rows exist in the 2016–2025 output. `init_db.py` handles this correctly (returns `None`).

### 3. Livret A statements not supported
PDFs in `bankStatements/pdf/Livret_all/` have a different layout and are not processed by either extractor.

### 4. Long concatenated descriptions (2014–2016)
In the wide-format PDFs, multi-line transaction details (reference numbers, mandate IDs, etc.) are concatenated into the `DATE LIBELLE` field. The core description is always at the start; everything after the first line is reference metadata.
