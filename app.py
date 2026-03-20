# app.py
from __future__ import annotations
import json
import os
import sqlite3
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, Response, g, jsonify, render_template, request, stream_with_context
from openai import OpenAI

from init_db import create_db

load_dotenv()

app = Flask(__name__)
DB_PATH = os.environ.get("DB_PATH", "financial.db")


# ── DB helpers ──────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/conversations")
def list_conversations():
    db = get_db()
    rows = db.execute(
        "SELECT id, title, created_at FROM conversations ORDER BY created_at DESC"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/conversations")
def create_conversation():
    title = (request.json or {}).get("title", "New Chat")[:50]
    db = get_db()
    cur = db.execute(
        "INSERT INTO conversations (title, created_at) VALUES (?, ?)",
        (title, _now()),
    )
    db.commit()
    return jsonify({"id": cur.lastrowid, "title": title}), 201


@app.get("/conversations/<int:conv_id>")
def get_conversation(conv_id: int):
    db = get_db()
    conv = db.execute("SELECT id, title FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    if not conv:
        return jsonify({"error": "not found"}), 404
    messages = db.execute(
        "SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at",
        (conv_id,),
    ).fetchall()
    return jsonify({"id": conv["id"], "title": conv["title"], "messages": [dict(m) for m in messages]})


@app.delete("/conversations/<int:conv_id>")
def delete_conversation(conv_id: int):
    db = get_db()
    db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    db.commit()
    return jsonify({"ok": True})


SYSTEM_PROMPT = """You are a personal financial assistant for LCL bank account data spanning 2014–2025.
The SQLite database has a `transactions` table with these columns:
  transaction_date TEXT (YYYY-MM-DD format)
  value_date TEXT (YYYY-MM-DD format)
  description TEXT (full transaction description)
  debit REAL (money out — NULL if this is income)
  credit REAL (money in — NULL if this is a payment)
  category TEXT (one of: groceries, rent, transport, income, cash, utilities, entertainment, health, transfers, other)
  source_file TEXT
  source_row_index INTEGER

All amounts are in euros. Use the query_db tool to look up data before answering.
Be concise. Format monetary amounts with € symbol and 2 decimal places.
When showing multiple transactions, use a markdown table."""

QUERY_DB_TOOL = {
    "type": "function",
    "function": {
        "name": "query_db",
        "description": (
            "Execute a read-only SQL SELECT query against the financial database. "
            "Use this to look up transactions, compute totals, filter by category, date range, etc."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "A SELECT statement only. No INSERT, UPDATE, DELETE, or DROP.",
                }
            },
            "required": ["sql"],
        },
    },
}


MODEL = os.environ.get("MODEL", "google/gemini-3-flash-preview")


def get_ai_client() -> OpenAI:
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
    )


def _run_query(sql: str) -> list[dict]:
    """Execute a read-only SQL query. Raises on non-SELECT or error."""
    stripped = sql.strip().upper()
    if not (stripped.startswith("SELECT") or stripped.startswith("WITH")):
        raise ValueError("Only SELECT statements are allowed")
    # Real protection: mode=ro rejects any write operation at the driver level.
    # Open read-only connection
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


@app.post("/conversations/<int:conv_id>/chat")
def chat(conv_id: int):
    db = get_db()
    conv = db.execute("SELECT id FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    if not conv:
        return jsonify({"error": "not found"}), 404

    user_message = (request.json or {}).get("message", "").strip()
    if not user_message:
        return jsonify({"error": "message required"}), 400

    # Save user message immediately
    db.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, 'user', ?, ?)",
        (conv_id, user_message, _now()),
    )
    # Set conversation title from first message if still default
    first_msg = db.execute(
        "SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conv_id,)
    ).fetchone()[0]
    if first_msg == 1:
        title = user_message[:50]
        db.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conv_id))
    db.commit()

    # Load last 20 user/assistant messages for context
    history = db.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY created_at DESC LIMIT 20",
        (conv_id,),
    ).fetchall()
    messages = [{"role": r["role"], "content": r["content"]} for r in reversed(history)]

    def generate():
        try:
            client = get_ai_client()
            full_response = ""

            current_messages = messages[:]
            while True:
                tool_calls_acc = {}   # index → {id, name, args}
                finish_reason = None

                stream = client.chat.completions.create(
                    model=MODEL,
                    max_tokens=2048,
                    messages=[{"role": "system", "content": SYSTEM_PROMPT}] + current_messages,
                    tools=[QUERY_DB_TOOL],
                    stream=True,
                )

                for chunk in stream:
                    choice = chunk.choices[0]
                    if choice.delta.content:
                        yield _sse({"type": "token", "text": choice.delta.content})
                        full_response += choice.delta.content
                    if choice.delta.tool_calls:
                        for tc in choice.delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {"id": tc.id, "name": tc.function.name, "args": ""}
                            if tc.function.arguments:
                                tool_calls_acc[idx]["args"] += tc.function.arguments
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason

                if finish_reason == "tool_calls":
                    tool_calls = [
                        {"id": v["id"], "type": "function",
                         "function": {"name": v["name"], "arguments": v["args"]}}
                        for _, v in sorted(tool_calls_acc.items(), key=lambda kv: kv[0])
                    ]
                    current_messages.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": tool_calls,
                    })
                    for tc in tool_calls:
                        sql = json.loads(tc["function"]["arguments"]).get("sql", "")
                        yield _sse({"type": "tool_call", "sql": sql})
                        try:
                            rows = _run_query(sql)
                            yield _sse({"type": "tool_result", "rows": len(rows)})
                            current_messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": json.dumps(rows),
                            })
                        except Exception as e:
                            yield _sse({"type": "tool_error", "message": str(e)})
                            current_messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": f"Error: {e}",
                            })
                    full_response = ""  # reset; only final text-generating iteration saved
                else:
                    break

            # Save complete assistant response
            db2 = sqlite3.connect(DB_PATH)
            db2.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, 'assistant', ?, ?)",
                (conv_id, full_response, _now()),
            )
            db2.commit()
            db2.close()

            yield _sse({"type": "done"})
        except Exception as e:
            yield _sse({"type": "error", "message": str(e)})

    def generate_safe():
        yield from generate()

    return Response(
        stream_with_context(generate_safe()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    # Ensure DB exists (create schema if first run)
    conn = create_db(DB_PATH)
    conn.close()
    app.run(debug=False, port=5000)
