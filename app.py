# app.py
from __future__ import annotations
import json
import os
import sqlite3
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, Response, g, jsonify, render_template, request, stream_with_context
import anthropic

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
