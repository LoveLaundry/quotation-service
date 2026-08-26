"""
Public chatbot conversations.

- Guests (public.lovelaundry.lk) post messages; the service stores them and,
  unless an admin has taken over, asks lovelaundry-bot for a reply.
- Admins (quotation-ui) can list every conversation, read the full history,
  take over a conversation (which suppresses the bot), and reply as themselves.
- The public widget polls its conversation to learn who it is talking to
  (bot vs a named admin).
"""
import json
import os
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..auth_helper import get_current_user, require_role
from ..database.main_db import chat_collection

router = APIRouter(prefix="/chat", tags=["chat"])

# lovelaundry-bot (stateless reply engine). Optional API key via CHAT_API_KEY.
BOT_URL = os.getenv("CHAT_BOT_URL")
BOT_API_KEY = os.getenv("CHAT_API_KEY")

# Fallback when the bot is unreachable / unconfigured.
BOT_FALLBACK = "Thank you for your message. Our team will get back to you shortly."


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_message(sender: str, text: str, sender_name: Optional[str] = None) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "sender": sender,  # guest | bot | admin
        "text": text,
        "sender_name": sender_name,
        "timestamp": _now().isoformat(),
    }


def _call_bot(message: str, lang: str = "en") -> str:
    if not BOT_URL:
        return BOT_FALLBACK
    payload = json.dumps({"message": message, "lang": lang}).encode("utf-8")
    req = urllib.request.Request(
        f"{BOT_URL.rstrip('/')}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if BOT_API_KEY:
        req.add_header("x-api-key", BOT_API_KEY)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("reply") or BOT_FALLBACK
    except Exception:
        return BOT_FALLBACK


def _serialize(doc: dict) -> dict:
    return {
        "conversation_id": doc["_id"],
        "guest_id": doc.get("guest_id"),
        "guest_name": doc.get("guest_name"),
        "assigned_admin_name": doc.get("assigned_admin_name"),
        "status": doc.get("status", "open"),
        "messages": doc.get("messages", []),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


def _get_or_create(conversation_id: str, guest_id: str, guest_name: Optional[str]) -> dict:
    doc = chat_collection.find_one({"_id": conversation_id})
    if doc:
        return doc
    now = _now()
    doc = {
        "_id": conversation_id,
        "guest_id": guest_id,
        "guest_name": guest_name,
        "assigned_admin_name": None,
        "status": "open",
        "messages": [],
        "created_at": now,
        "updated_at": now,
    }
    chat_collection.insert_one(doc)
    return doc


# ── Public endpoints (no auth) ───────────────────────────────────────────────
class GuestMessageIn(BaseModel):
    message: str
    guest_id: str
    lang: str = "en"
    guest_name: Optional[str] = None


@router.post("/conversations/{conversation_id}/messages")
def post_guest_message(conversation_id: str, body: GuestMessageIn):
    """Guest sends a message. Bot replies unless an admin has taken over."""
    doc = _get_or_create(conversation_id, body.guest_id, body.guest_name)

    new_messages = [_new_message("guest", body.message)]
    bot_replied = False
    if not doc.get("assigned_admin_name"):
        reply = _call_bot(body.message, body.lang)
        if reply:
            new_messages.append(_new_message("bot", reply))
            bot_replied = True

    set_fields: dict = {"updated_at": _now()}
    if body.guest_name and not doc.get("guest_name"):
        set_fields["guest_name"] = body.guest_name

    chat_collection.update_one(
        {"_id": conversation_id},
        {"$push": {"messages": {"$each": new_messages}}, "$set": set_fields},
    )
    updated = chat_collection.find_one({"_id": conversation_id})
    return {
        "conversation_id": conversation_id,
        "messages": updated["messages"],
        "assigned_admin_name": updated.get("assigned_admin_name"),
        "bot_replied": bot_replied,
    }


@router.get("/conversations/{conversation_id}")
def get_guest_conversation(
    conversation_id: str,
    guest_id: Optional[str] = Query(None),
):
    """Public history poll. Guest uses this to learn new messages and who
    is handling the conversation."""
    doc = chat_collection.find_one({"_id": conversation_id})
    if not doc:
        return {
            "conversation_id": conversation_id,
            "messages": [],
            "assigned_admin_name": None,
        }
    return _serialize(doc)


# ── Admin endpoints (ADMIN / MANAGER) ────────────────────────────────────────
@router.get(
    "/admin/conversations",
    dependencies=[Depends(require_role(["ADMIN", "MANAGER"]))],
)
def admin_list_conversations(
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None),
):
    filt: dict = {}
    if status:
        filt["status"] = status
    docs = list(chat_collection.find(filt).sort("updated_at", -1).limit(limit))
    result = []
    for d in docs:
        messages = d.get("messages", [])
        result.append(
            {
                "conversation_id": d["_id"],
                "guest_id": d.get("guest_id"),
                "guest_name": d.get("guest_name"),
                "assigned_admin_name": d.get("assigned_admin_name"),
                "status": d.get("status", "open"),
                "message_count": len(messages),
                "last_message": messages[-1] if messages else None,
                "created_at": d.get("created_at"),
                "updated_at": d.get("updated_at"),
            }
        )
    return result


@router.get(
    "/admin/conversations/{conversation_id}",
    dependencies=[Depends(require_role(["ADMIN", "MANAGER"]))],
)
def admin_get_conversation(conversation_id: str):
    doc = chat_collection.find_one({"_id": conversation_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return _serialize(doc)


class AdminMessageIn(BaseModel):
    text: str


@router.post(
    "/admin/conversations/{conversation_id}/messages",
    dependencies=[Depends(require_role(["ADMIN", "MANAGER"]))],
)
def admin_post_message(
    conversation_id: str,
    body: AdminMessageIn,
    current_user: dict = Depends(get_current_user),
):
    doc = chat_collection.find_one({"_id": conversation_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Conversation not found")
    sender_name = (
        current_user.get("user_name")
        or current_user.get("name")
        or current_user.get("sub")
        or "Admin"
    )
    admin_msg = _new_message("admin", body.text, sender_name=sender_name)
    chat_collection.update_one(
        {"_id": conversation_id},
        {
            "$push": {"messages": admin_msg},
            "$set": {"assigned_admin_name": sender_name, "updated_at": _now()},
        },
    )
    updated = chat_collection.find_one({"_id": conversation_id})
    return _serialize(updated)


class AdminUpdateIn(BaseModel):
    status: Optional[str] = None
    guest_name: Optional[str] = None
    assigned_admin_name: Optional[str] = None


@router.patch(
    "/admin/conversations/{conversation_id}",
    dependencies=[Depends(require_role(["ADMIN", "MANAGER"]))],
)
def admin_update_conversation(conversation_id: str, body: AdminUpdateIn):
    doc = chat_collection.find_one({"_id": conversation_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Conversation not found")
    set_fields: dict = {}
    if body.status is not None:
        set_fields["status"] = body.status
    if body.guest_name is not None:
        set_fields["guest_name"] = body.guest_name
    if body.assigned_admin_name is not None:
        set_fields["assigned_admin_name"] = body.assigned_admin_name
    if set_fields:
        set_fields["updated_at"] = _now()
    chat_collection.update_one({"_id": conversation_id}, {"$set": set_fields})
    updated = chat_collection.find_one({"_id": conversation_id})
    return _serialize(updated)
