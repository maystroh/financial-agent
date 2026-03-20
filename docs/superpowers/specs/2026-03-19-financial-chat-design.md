# Financial Chat App — Design Spec
_Date: 2026-03-19_

## Overview

A local web app that loads LCL bank statement CSVs into SQLite, auto-categorizes transactions with Claude, and provides a persistent chat interface to query financial history.

Two deliverables:
1. **`init_db.py`** — one-time import script
2. **`app.py`** — Flask web app

---

## Data Sources

| Source | Relative path | Period |
|--------|---------------|--------|
| 2014–2016 current account | `bankStatements/2014_2016_current_account_output/comptes_full.csv` | 2014–2016 |
| 2016–2025 current account | `bankStatements/2016_2025_current_account_output/comptes_full.csv` | 2016–2025 |

Raw CSV schema: `DATE LIBELLE, BLANK1, BLANK2, VALEUR, DEBIT, CREDIT`

The two files may overlap slightly around early 2016. Deduplication is handled by a UNIQUE constraint (see schema).

---

## Database Schema (`financial.db`)

### `transactions`
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| transaction_date | TEXT | ISO date from `DATE LIBELLE` prefix DD.MM, year from VALEUR (YYYY-MM-DD) |
| value_date | TEXT | ISO date from VALEUR field DD.MM.YY (YYYY-MM-DD) |
| description | TEXT | Merged DATE LIBELLE + BLANK1 + BLANK2 continuation lines, joined with space |
| debit | REAL | NULL if credit transaction |
| credit | REAL | NULL if debit transaction |
| category | TEXT | Claude-assigned category |
| source_file | TEXT | Relative path of origin CSV (e.g. `bankStatements/2016_2025.../comptes_full.csv`) |
| source_row_index | INTEGER | 0-based index of the anchor row in the source CSV (used for dedup) |

**UNIQUE constraint:** `(source_file, source_row_index)` — prevents re-importing the same row on repeated runs. The two source CSVs cover non-overlapping date ranges (2014–April 2016 and May 2016–2025), so no cross-file deduplication is needed.

**Categories:** `groceries`, `rent`, `transport`, `income`, `cash`, `utilities`, `entertainment`, `health`, `transfers`, `other`

### `conversations`
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| title | TEXT | First 50 chars of first user message (naive truncation, no extra API call) |
| created_at | TEXT | ISO timestamp |

### `messages`
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| conversation_id | INTEGER FK | References conversations.id ON DELETE CASCADE |
| role | TEXT | `user` or `assistant` |
| content | TEXT | Full message content |
| created_at | TEXT | ISO timestamp |

`PRAGMA foreign_keys = ON` must be set on each connection so cascade deletes fire. Mobile/responsive layout is out of scope — the UI targets desktop only.

---

## Init Script (`init_db.py`)

### Multi-line Row Collapse

An **anchor row** is any row with a non-empty `VALEUR` field. Rows with an empty `VALEUR` are continuation lines that belong to the preceding anchor row. The collapse algorithm:

```
current_anchor = None
for each row in CSV:
    if row.VALEUR is not empty:
        if current_anchor: emit current_anchor
        current_anchor = row
    else:
        append row.DATE_LIBELLE (and BLANK1/BLANK2 if non-empty) to current_anchor.description with a space separator
emit current_anchor if pending
```

After collapse, the description for each transaction is the space-joined concatenation of all lines from that group.

### Date Parsing

- **VALEUR** format: `DD.MM.YY` → parsed with `datetime.strptime(v, "%d.%m.%y")`. Python's `%y` maps 00–68 → 2000–2068, 69–99 → 1969–1999. This is correct for the 2014–2025 dataset.
- **transaction_date**: extracted from the `D{1,2}.MM` prefix of `DATE LIBELLE` using regex `^(\d{1,2})\.(\d{2})` (both single-digit and zero-padded days exist in the data, e.g. `5.11` and `04.04`). Year taken from VALEUR with one adjustment: if the transaction month > value month (e.g. transaction is Dec, value date is Jan), the year is `VALEUR_year - 1` to handle year-boundary transactions. Stored as ISO `YYYY-MM-DD`.
- **value_date**: directly from VALEUR, stored as ISO `YYYY-MM-DD`.

### Amount Normalization

French locale: `"1 234,56"` → remove spaces → replace comma with period → `float("1234.56")`. Empty strings become `None`.

### Categorization Batching

There are approximately **~6,100 anchor transactions** after multi-line collapse (~11,300 raw CSV rows, roughly half of which are continuation lines). Transactions are sent to Claude in batches of **200 rows** (~31 API calls total; 200 rows of descriptions ≈ 4–8k tokens, well within limits).

**Request format per batch** — each transaction serialized as one line:
```
{id}|{description}|{debit_or_credit}
```

**Prompt:**
```
Categorize each transaction below into exactly one category from this list:
groceries, rent, transport, income, cash, utilities, entertainment, health, transfers, other

Return a JSON array where each element is {"id": <id>, "category": "<category>"}.
Do not include any other text.

Transactions:
1|CB LECLERC 01/04/16|debit:3.45
2|PRLV SEPA LUXIOR IMMOBILIER Appel de loyer|debit:270.00
...
```

**Response parsing:** parse JSON array, map `id → category`. On parse failure for a batch, fall back to `"other"` for all transactions in that batch and log a warning.

### Idempotency

The `transactions` table is created with `CREATE TABLE IF NOT EXISTS`. Rows are inserted with `INSERT OR IGNORE` (the UNIQUE constraint silently skips duplicates). This means:
- Re-running the script will not re-categorize already-imported rows (no wasted API calls).
- Overlapping date ranges between the two source CSVs are handled automatically.
- To fully re-import from scratch, the user must manually delete `financial.db` first.

---

## Web App (`app.py`)

### Stack
- **Flask** — web framework
- **anthropic** Python SDK — `claude-sonnet-4-6`
- **SQLite** (stdlib `sqlite3`) — persistence
- **Vanilla JS + SSE** — streaming, no JS framework

### Routes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Main page |
| GET | `/conversations` | List all conversations, newest first (JSON) |
| POST | `/conversations` | Create new conversation (JSON) |
| GET | `/conversations/<id>` | Load all messages for a conversation (JSON) |
| POST | `/conversations/<id>/chat` | Send message, stream response (SSE) |
| DELETE | `/conversations/<id>` | Delete a conversation and its messages (cascade) |

### Chat Flow & SSE Streaming

The `/conversations/<id>/chat` endpoint returns an SSE stream. The flow handles Claude's multi-round tool-use:

```
1. Save user message to DB
2. Load last 20 messages from DB as context
3. POST to Claude with query_db tool definition
4. Stream response events to client:
   - data: {"type": "thinking"}         ← while Claude is reasoning / calling tools
   - data: {"type": "tool_call", "sql": "..."}  ← when Claude issues a query_db call
   - data: {"type": "tool_result", "rows": N}   ← after executing the query
   - data: {"type": "token", "text": "..."}     ← each streamed text token
   - data: {"type": "done"}                     ← stream complete
5. Save complete assistant response to DB only on `done` (not on partial/dropped connections).
   If the SSE connection drops before `done`, the user message is persisted but no assistant message is saved. The broken state is recoverable — the user can simply send another message.
   Tool call/result turns are ephemeral (not stored in `messages`). Only `role=user` and `role=assistant` text messages are persisted and counted toward the 20-message context window.
```

Claude may call `query_db` multiple times before producing final text. The client shows a "thinking..." indicator until the first `token` event arrives.

### `query_db` Tool

```python
{
  "name": "query_db",
  "description": "Execute a read-only SQL SELECT query against the financial database. The transactions table has columns: id, transaction_date (YYYY-MM-DD), value_date, description, debit, credit, category, source_file.",
  "input_schema": {
    "type": "object",
    "properties": {
      "sql": {"type": "string", "description": "A SELECT statement only. No writes."}
    },
    "required": ["sql"]
  }
}
```

**Read-only enforcement:** DB opened with `sqlite3.connect("file:financial.db?mode=ro", uri=True)`. Any non-SELECT statement will raise an OperationalError which is caught and returned as a tool error (never crashes the app).

### System Prompt

```
You are a personal financial assistant for LCL bank account data spanning 2014–2025.
The SQLite database has a `transactions` table with columns:
  transaction_date TEXT (YYYY-MM-DD), value_date TEXT, description TEXT,
  debit REAL (money out), credit REAL (money in), category TEXT, source_file TEXT.
Categories: groceries, rent, transport, income, cash, utilities, entertainment, health, transfers, other.
All amounts are in euros. Use the query_db tool to look up data before answering.
Be concise. Format monetary answers with € and 2 decimal places.
```

### Error Handling

- Missing `ANTHROPIC_API_KEY`: Flask returns 500 with a plain-text error message; UI displays it in the chat bubble as a red error message.
- API failure mid-stream: SSE event `{"type": "error", "message": "..."}` closes the stream; UI displays the error inline.
- DB query error from Claude: tool returns `{"error": "<message>"}` and Claude is expected to rephrase or retry.

---

## UI Layout

Single-page, two-panel layout:

```
┌─────────────────┬──────────────────────────────────────┐
│   SIDEBAR       │  CHAT PANEL                          │
│                 │                                      │
│ [+ New Chat]    │  Conversation title                  │
│                 │  ────────────────                    │
│ > Spending 2023 │  [user bubble]                       │
│   Rent analysis │  [assistant bubble — markdown]       │
│   ...           │  [thinking... indicator]             │
│                 │                                      │
│                 │  ────────────────────────────────    │
│                 │  [ Type your question...      ] [→]  │
└─────────────────┴──────────────────────────────────────┘
```

- Sidebar: conversations newest-first, click to load, active conversation highlighted
- Assistant bubbles render markdown (tables, bold, bullets) via **marked.js v9+** (loaded from CDN, no build step)
- Streaming: response appears token-by-token; "thinking..." shown during tool-use phase
- Enter to send, Shift+Enter for newline
- Conversation title = first 50 chars of first user message (set on first message)

---

## File Structure

```
financial-agent/
├── init_db.py
├── app.py
├── templates/
│   └── index.html
├── static/
│   └── style.css
└── financial.db        # generated by init_db.py
```

---

## Dependencies

```
pip install flask anthropic pandas
```

Requires `ANTHROPIC_API_KEY` environment variable.
