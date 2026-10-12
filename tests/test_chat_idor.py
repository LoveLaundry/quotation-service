"""Public chat endpoints must not leak others' conversations (IDOR guard).

A guest who knows/guesses a conversation id must NOT be able to read its
history, post into it, or join it — unless they are a participant (or are
invited by one).
"""
import os
import uuid

# Must be set before the service package is imported: crypto_helper refuses
# to start without a MASTER_KEY, and auth_helper without a JWT_SECRET.
os.environ.setdefault("MASTER_KEY", "test-only-master-key-0123456789abcdef")
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-0123456789abcdef")
os.environ.setdefault("DATABASE_URL", "mongodb://127.0.0.1:27017")

import mongomock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from quotation_service.routers import chat as chat_module


@pytest.fixture
def db(monkeypatch):
    conv = mongomock.MongoClient().get_database("test").get_collection("chat_conversations")
    msgs = mongomock.MongoClient().get_database("test").get_collection("chat_messages")
    monkeypatch.setattr(chat_module, "chat_collection", conv)
    monkeypatch.setattr(chat_module, "chat_messages_collection", msgs)
    monkeypatch.setattr(chat_module, "_call_bot", lambda *a, **k: "Thanks!")
    app = FastAPI()
    app.include_router(chat_module.router)
    return {"client": TestClient(app), "conv": conv, "msgs": msgs}


def _conv_id():
    return str(uuid.uuid4())


def test_owner_can_create_and_read(db):
    cid = _conv_id()
    r = db["client"].post(
        f"/chat/conversations/{cid}/messages",
        json={"message": "hi", "guest_id": "g-owner", "name": "Owner"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["created"] is True
    assert len(r.json()["messages"]) == 2  # guest + bot reply

    r = db["client"].get(f"/chat/conversations/{cid}", params={"guest_id": "g-owner"})
    assert r.status_code == 200
    assert r.json()["messages"][0]["text"] == "hi"


def test_stranger_may_not_read_or_post(db):
    cid = _conv_id()
    assert db["client"].post(
        f"/chat/conversations/{cid}/messages",
        json={"message": "hi", "guest_id": "g-owner", "name": "Owner"},
    ).status_code == 200

    # Stranger read -> 403
    r = db["client"].get(f"/chat/conversations/{cid}", params={"guest_id": "g-evil"})
    assert r.status_code == 403

    # Stranger post -> 403 (conversation already created by owner)
    r = db["client"].post(
        f"/chat/conversations/{cid}/messages",
        json={"message": "let me in", "guest_id": "g-evil"},
    )
    assert r.status_code == 403


def test_missing_guest_id_is_rejected(db):
    cid = _conv_id()
    r = db["client"].get(f"/chat/conversations/{cid}")
    assert r.status_code == 422  # guest_id is now required


def test_self_join_without_inviter_is_forbidden(db):
    cid = _conv_id()
    db["client"].post(
        f"/chat/conversations/{cid}/messages",
        json={"message": "hi", "guest_id": "g-owner", "name": "Owner"},
    )

    r = db["client"].post(
        f"/chat/conversations/{cid}/participants",
        json={"guest_id": "g-evil", "name": "Evil"},
    )
    assert r.status_code == 403

    # Even a fake inviter who is not a participant gets 403.
    r = db["client"].post(
        f"/chat/conversations/{cid}/participants",
        json={"guest_id": "g-evil", "name": "Evil", "inviter_guest_id": "g-spoof"},
    )
    assert r.status_code == 403


def test_invited_guest_gains_access(db):
    cid = _conv_id()
    db["client"].post(
        f"/chat/conversations/{cid}/messages",
        json={"message": "hi", "guest_id": "g-owner", "name": "Owner"},
    )

    r = db["client"].post(
        f"/chat/conversations/{cid}/participants",
        json={"guest_id": "g-friend", "name": "Friend", "inviter_guest_id": "g-owner"},
    )
    assert r.status_code == 200, r.text

    # Now the friend can read and post.
    r = db["client"].get(f"/chat/conversations/{cid}", params={"guest_id": "g-friend"})
    assert r.status_code == 200
    r = db["client"].post(
        f"/chat/conversations/{cid}/messages",
        json={"message": "I'm here", "guest_id": "g-friend", "name": "Friend"},
    )
    assert r.status_code == 200