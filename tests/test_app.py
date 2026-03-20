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
