# tests/test_app.py
import json
import pytest
import tempfile
import os

os.environ.setdefault("OPENROUTER_API_KEY", "test-or-key")

@pytest.fixture
def app(tmp_path):
    db_path = str(tmp_path / "test.db")
    import app as flask_app
    flask_app.DB_PATH = db_path
    # Reset DB for each test
    from init_db import create_db
    conn = create_db(db_path)
    conn.close()
    flask_app.app.config["TESTING"] = True
    with flask_app.app.app_context():
        yield flask_app.app

@pytest.fixture
def client(app):
    return app.test_client()

def test_index_returns_200(client):
    resp = client.get("/")
    assert resp.status_code == 200

def test_list_conversations_empty(client):
    resp = client.get("/conversations")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data == []

def test_create_conversation(client):
    resp = client.post("/conversations", json={"title": "Test Chat"})
    assert resp.status_code == 201
    data = json.loads(resp.data)
    assert data["id"] == 1
    assert data["title"] == "Test Chat"

def test_list_conversations_after_create(client):
    client.post("/conversations", json={"title": "First"})
    client.post("/conversations", json={"title": "Second"})
    resp = client.get("/conversations")
    data = json.loads(resp.data)
    assert len(data) == 2
    assert data[0]["title"] == "Second"  # newest first

def test_get_conversation_messages_empty(client):
    client.post("/conversations", json={"title": "Test"})
    resp = client.get("/conversations/1")
    data = json.loads(resp.data)
    assert data["messages"] == []

def test_delete_conversation(client):
    client.post("/conversations", json={"title": "ToDelete"})
    resp = client.delete("/conversations/1")
    assert resp.status_code == 200
    resp2 = client.get("/conversations")
    assert json.loads(resp2.data) == []

def test_get_nonexistent_conversation(client):
    resp = client.get("/conversations/999")
    assert resp.status_code == 404

# Add to tests/test_app.py
from unittest.mock import patch, MagicMock

def _make_conv(client):
    resp = client.post("/conversations", json={"title": "Test"})
    return json.loads(resp.data)["id"]

def _drain_sse(resp) -> str:
    """Fully consume a streaming SSE response and return the body as a string.

    Flask's test client returns a lazy ``Response`` for streaming endpoints.
    Calling ``resp.data`` triggers a read, but we must also exhaust the
    underlying generator so that any end-of-stream side-effects (e.g. the
    assistant DB write inside ``generate()``) complete before we assert.
    """
    return resp.data.decode()


def test_chat_saves_user_message(client):
    conv_id = _make_conv(client)
    mock_client = MagicMock()
    text_chunk = MagicMock()
    text_chunk.choices = [MagicMock()]
    text_chunk.choices[0].delta.content = "You spent €100."
    text_chunk.choices[0].delta.tool_calls = None
    text_chunk.choices[0].finish_reason = None
    stop_chunk = MagicMock()
    stop_chunk.choices = [MagicMock()]
    stop_chunk.choices[0].delta.content = None
    stop_chunk.choices[0].delta.tool_calls = None
    stop_chunk.choices[0].finish_reason = "stop"
    mock_client.chat.completions.create.return_value = iter([text_chunk, stop_chunk])

    with patch("app.get_ai_client", return_value=mock_client):
        resp = client.post(
            f"/conversations/{conv_id}/chat",
            json={"message": "How much did I spend?"},
        )
    assert resp.status_code == 200
    # Drain the SSE stream so generate() runs to completion (including the
    # assistant DB write that happens after the streaming loop).
    _drain_sse(resp)

    # Verify both messages were saved
    conv = json.loads(client.get(f"/conversations/{conv_id}").data)
    roles = [m["role"] for m in conv["messages"]]
    assert "user" in roles
    assert "assistant" in roles

def test_chat_returns_sse_stream(client):
    conv_id = _make_conv(client)
    mock_client = MagicMock()
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta.content = "Hello"
    chunk.choices[0].delta.tool_calls = None
    chunk.choices[0].finish_reason = None
    stop = MagicMock()
    stop.choices = [MagicMock()]
    stop.choices[0].delta.content = None
    stop.choices[0].delta.tool_calls = None
    stop.choices[0].finish_reason = "stop"
    mock_client.chat.completions.create.return_value = iter([chunk, stop])

    with patch("app.get_ai_client", return_value=mock_client):
        resp = client.post(
            f"/conversations/{conv_id}/chat",
            json={"message": "Hi"},
        )

    # Drain stream before asserting so generate() completes cleanly
    body = _drain_sse(resp)
    assert "token" in body

def test_chat_404_for_nonexistent_conv(client):
    with patch("app.get_ai_client", return_value=MagicMock()):
        resp = client.post("/conversations/999/chat", json={"message": "hi"})
    assert resp.status_code == 404
