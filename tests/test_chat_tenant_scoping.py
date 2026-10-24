"""Admin chat routes must be tenant scoped.

Guest conversations hold names and message text. The admin routes that list
and read them used to query the raw collection, so an admin of one laundry
could read the guest conversations of another laundry. The admin routes now go
through the tenant-scoped handle, while the guest routes keep using the raw
collection (a guest has no session, so no tenant can be derived for them).
"""
import os
import uuid

os.environ.setdefault("MASTER_KEY", "test-only-master-key-0123456789abcdef")
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-0123456789abcdef")
os.environ.setdefault("DATABASE_URL", "mongodb://127.0.0.1:27017")

import mongomock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from quotation_service.auth_helper import create_access_token
from quotation_service.routers import chat as chat_module
from quotation_service.tenant_scope import TenantClaimsMiddleware


def _token(laundry_id: str) -> str:
    return create_access_token(
        {
            "user_id": "u-1",
            "role": "ADMIN",
            "laundry_id": laundry_id,
            "laundries": [{"id": laundry_id, "status": "ACTIVE"}],
        }
    )


@pytest.fixture
def db(monkeypatch):
    conv = mongomock.MongoClient().get_database("test").get_collection("chat_conversations")
    msgs = mongomock.MongoClient().get_database("test").get_collection("chat_messages")
    monkeypatch.setattr(chat_module, "chat_collection", conv)
    monkeypatch.setattr(chat_module, "chat_messages_collection", msgs)
    monkeypatch.setattr(chat_module, "_call_bot", lambda *a, **k: "Thanks!")
    app = FastAPI()
    app.add_middleware(TenantClaimsMiddleware)
    app.include_router(chat_module.router)
    return {"client": TestClient(app), "conv": conv, "msgs": msgs}


def _seed_conversation(db, conversation_id: str, guest_name: str):
    r = db["client"].post(
        f"/chat/conversations/{conversation_id}/messages",
        json={"message": "hello", "guest_id": "g-owner", "name": guest_name},
    )
    assert r.status_code == 200, r.text
    return conversation_id


def test_admin_of_another_laundry_cannot_list_or_read_the_conversation(db):
    conv_id = _seed_conversation(db, str(uuid.uuid4()), "Private Guest")

    foreign = {"Authorization": f"Bearer {_token('otherlaundry')}"}
    owner = {"Authorization": f"Bearer {_token('lovelaundry')}"}

    # The owning laundry sees it.
    r = db["client"].get("/chat/admin/conversations", headers=owner)
    assert r.status_code == 200, r.text
    assert [c["conversation_id"] for c in r.json()] == [conv_id]

    # Another laundry does not.
    r = db["client"].get("/chat/admin/conversations", headers=foreign)
    assert r.status_code == 200, r.text
    assert r.json() == []

    r = db["client"].get(f"/chat/admin/conversations/{conv_id}", headers=foreign)
    assert r.status_code == 404
    assert "Private Guest" not in r.text


def test_admin_of_another_laundry_cannot_write_into_the_conversation(db):
    conv_id = _seed_conversation(db, str(uuid.uuid4()), "Private Guest")
    foreign = {"Authorization": f"Bearer {_token('otherlaundry')}"}

    r = db["client"].post(
        f"/chat/admin/conversations/{conv_id}/messages",
        json={"text": "hello there"},
        headers=foreign,
    )
    assert r.status_code == 404

    r = db["client"].patch(
        f"/chat/admin/conversations/{conv_id}",
        json={"status": "closed"},
        headers=foreign,
    )
    assert r.status_code == 404

    # Nothing was written.
    stored = db["conv"].find_one({"_id": conv_id})
    assert stored.get("status") == "open"
    assert db["msgs"].count_documents({"conversation_id": conv_id}) == 2


def test_owning_admin_can_reply_and_rename(db):
    conv_id = _seed_conversation(db, str(uuid.uuid4()), "Own Guest")
    owner = {"Authorization": f"Bearer {_token('lovelaundry')}"}

    r = db["client"].post(
        f"/chat/admin/conversations/{conv_id}/messages",
        json={"text": "We are on it"},
        headers=owner,
    )
    assert r.status_code == 200, r.text
    assert r.json()["assigned_admin_name"]

    r = db["client"].patch(
        f"/chat/admin/conversations/{conv_id}",
        json={"title": "Order 42"},
        headers=owner,
    )
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Order 42"


def test_guest_routes_are_unaffected_by_tenancy(db):
    """A guest has no session, so the guest routes keep working as before."""
    conv_id = _seed_conversation(db, str(uuid.uuid4()), "Guest")

    r = db["client"].get(f"/chat/conversations/{conv_id}", params={"guest_id": "g-owner"})
    assert r.status_code == 200, r.text
    assert r.json()["guest_name"] == "Guest"

    r = db["client"].get(f"/chat/conversations/{conv_id}", params={"guest_id": "g-evil"})
    assert r.status_code == 403


def test_oversized_guest_message_is_rejected(db):
    r = db["client"].post(
        f"/chat/conversations/{uuid.uuid4()}/messages",
        json={"message": "x" * 5000, "guest_id": "g-owner", "name": "Owner"},
    )
    assert r.status_code == 422
