# Financial Chat App — Design Spec
_Date: 2026-03-19_

## Overview

A local web app that loads LCL bank statement CSVs into SQLite, auto-categorizes transactions with Claude, and provides a persistent chat interface to query financial history.

Two deliverables:
1. **`init_db.py`** — one-time import script
2. **`app.py`** — Flask web app

---

## Data Sources

| Source | File | Period |
|--------|------|--------|
| 2014–2016 current account | `bankStatements/2014_2016_current_account_output/comptes_full.csv` | 2014–2016 |
| 2016–2025 current account | `bankStatements/2016_2025_current_account_output/comptes_full.csv` | 2016–2025 |

Raw CSV schema: `DATE LIBELLE, BLANK1, BLANK2, VALEUR, DEBIT, CREDIT`

---

## Database Schema (`financial.db`)

### `transactions`
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| date | TEXT | ISO date from VALEUR (YYYY-MM-DD) |
| description | TEXT | Merged DATE LIBELLE + BLANK1 + BLANK2 continuation lines |
| valeur | TEXT | Value date ISO |
| debit | REAL | NULL if credit transaction |
| credit | REAL | NULL if debit transaction |
| category | TEXT | Claude-assigned category |
| source_file | TEXT | Origin CSV filename |

**Categories:** `groceries`, `rent`, `transport`, `income`, `cash`, `utilities`, `entertainment`, `health`, `transfers`, `other`

### `conversations`
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| title | TEXT | Auto-generated from first user message (≤50 chars) |
| created_at | TEXT | ISO timestamp |

### `messages`
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| conversation_id | INTEGER FK | References conversations.id |
| role | TEXT | `user` or `assistant` |
| content | TEXT | Full message content |
| created_at | TEXT | ISO timestamp |

---

## Init Script (`init_db.py`)

### Pipeline

```
read comptes_full.csv (both sources)
  └─ collapse multi-line rows into single transactions
       (rows with empty VALEUR/DEBIT/CREDIT are continuation lines)
  └─ parse date: VALEUR field DD.MM.YY → YYYY-MM-DD
  └─ normalize amounts: "1 234,56" → 1234.56
  └─ batch transactions → Claude (single call, ~500 per batch)
       prompt: assign one category per transaction from fixed list
  └─ insert into transactions table
```

### Idempotency
Script is safe to re-run: drops and recreates the `transactions` table. Conversations and messages are preserved.

---

## Web App (`app.py`)

### Stack
- **Flask** — web framework
- **anthropic** Python SDK — Claude claude-sonnet-4-6
- **SQLite** (stdlib `sqlite3`) — persistence
- **Vanilla JS + SSE** — streaming responses, no JS framework

### Routes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Main page |
| GET | `/conversations` | List all conversations (JSON) |
| POST | `/conversations` | Create new conversation (JSON) |
| GET | `/conversations/<id>` | Load messages for a conversation (JSON) |
| POST | `/conversations/<id>/chat` | Send message, stream response (SSE) |
| DELETE | `/conversations/<id>` | Delete a conversation |

### Chat Flow

1. User POSTs message to `/conversations/<id>/chat`
2. App loads last 20 messages from DB as Claude context
3. Claude is given a `query_db` tool:
   ```python
   {
     "name": "query_db",
     "description": "Execute a read-only SQL query against the financial database.",
     "input_schema": {
       "type": "object",
       "properties": {
         "sql": {"type": "string", "description": "SELECT statement only"}
       },
       "required": ["sql"]
     }
   }
   ```
4. Claude reasons, calls `query_db` as needed (enforced read-only: only SELECT allowed)
5. Final text response streamed back via SSE
6. Both messages saved to DB

### System Prompt (summary)
Claude is told: it is a personal financial assistant for LCL bank data 2014–2025, the DB schema, available categories, and that amounts are in euros.

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
│   ...           │                                      │
│                 │  ────────────────────────────────    │
│                 │  [ Type your question...      ] [→]  │
└─────────────────┴──────────────────────────────────────┘
```

- Sidebar: conversations newest-first, click to load
- Assistant bubbles render markdown (tables, bold, bullets)
- Streaming: response appears word-by-word
- Enter to send, Shift+Enter for newline

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

No other dependencies. Requires `ANTHROPIC_API_KEY` env var.
