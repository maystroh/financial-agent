# tests/test_app.py
import json
import pytest
import tempfile
import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

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

def test_chat_saves_user_message(client):
    conv_id = _make_conv(client)
    mock_client = MagicMock()
    # Minimal mock: Claude returns a simple text response with no tool use
    mock_stream = MagicMock()
    mock_stream.__enter__ = lambda s: s
    mock_stream.__exit__ = MagicMock(return_value=False)
    mock_stream.__iter__ = MagicMock(return_value=iter([]))
    mock_stream.get_final_message.return_value = MagicMock(
        content=[MagicMock(type="text", text="You spent €100.")],
        stop_reason="end_turn",
    )
    mock_client.messages.stream.return_value = mock_stream

    with patch("app.get_anthropic_client", return_value=mock_client):
        resp = client.post(
            f"/conversations/{conv_id}/chat",
            json={"message": "How much did I spend?"},
        )
    assert resp.status_code == 200

    # Verify user message was saved
    conv = json.loads(client.get(f"/conversations/{conv_id}").data)
    roles = [m["role"] for m in conv["messages"]]
    assert "user" in roles
    assert "assistant" in roles

def test_chat_returns_sse_stream(client):
    conv_id = _make_conv(client)
    mock_client = MagicMock()
    mock_stream = MagicMock()
    mock_stream.__enter__ = lambda s: s
    mock_stream.__exit__ = MagicMock(return_value=False)

    from anthropic.types import RawContentBlockDeltaEvent, TextDelta
    delta_event = MagicMock(spec=RawContentBlockDeltaEvent)
    delta_event.type = "content_block_delta"
    delta_event.delta = MagicMock(spec=TextDelta)
    delta_event.delta.type = "text_delta"
    delta_event.delta.text = "Hello"

    mock_stream.__iter__ = MagicMock(return_value=iter([delta_event]))
    mock_stream.get_final_message.return_value = MagicMock(
        content=[MagicMock(type="text", text="Hello")],
        stop_reason="end_turn",
    )
    mock_client.messages.stream.return_value = mock_stream

    with patch("app.get_anthropic_client", return_value=mock_client):
        resp = client.post(
            f"/conversations/{conv_id}/chat",
            json={"message": "Hi"},
        )

    body = resp.data.decode()
    assert "token" in body

def test_chat_404_for_nonexistent_conv(client):
    with patch("app.get_anthropic_client", return_value=MagicMock()):
        resp = client.post("/conversations/999/chat", json={"message": "hi"})
    assert resp.status_code == 404
