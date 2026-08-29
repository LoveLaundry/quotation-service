"""
Public chatbot conversations (encrypted at rest).

- Every message's `text` and the conversation `guest_name` + `participants`
  are envelope-encrypted with MASTER_KEY (same scheme as bills/gatepasses).
- A conversation can be a 1:1 chat or a GROUP: `participants` holds the
  client-side members [{ guest_id, name }]; `title` is an optional group name.
- Guests (public.lovelaundry.lk) post messages; the service stores them and,
  unless an admin has taken over, asks lovelaundry-bot for a reply.
- Admins (quotation-ui) can list every conversation, read the full (decrypted)
  history + members, take over a conversation (which suppresses the bot),
  rename the group, and reply as themselves.
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
from ..crypto_helper import decrypt_dict, encrypt_dict
from ..database.main_db import chat_collection, chat_messages_collection

router = APIRouter(prefix="/chat", tags=["chat"])

# lovelaundry-bot (stateless reply engine). Optional API key via CHAT_API_KEY.
BOT_URL = os.getenv("CHAT_BOT_URL")
BOT_API_KEY = os.getenv("CHAT_API_KEY")

# Fallback when the bot is unreachable / unconfigured.
BOT_FALLBACK = "Thank you for your message. Our team will get back to you shortly."

SENSITIVE_CONV = ["guest_name", "participants"]
SENSITIVE_MSG = ["text"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_message(
    sender: str,
    text: str,
    sender_name: Optional[str] = None,
    sender_guest_id: Optional[str] = None,
) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "conversation_id": None,  # set by the caller
        "sender": sender,  # guest | bot | admin
        "sender_name": sender_name,
        "sender_guest_id": sender_guest_id,
        "text": text,
        "timestamp": _now(),
    }


def _insert_message(conversation_id: str, msg: dict) -> None:
    doc = encrypt_dict(
        {
            "id": msg["id"],
            "conversation_id": conversation_id,
            "sender": msg["sender"],
            "sender_name": msg["sender_name"],
            "sender_guest_id": msg.get("sender_guest_id"),
            "text": msg["text"],
            "timestamp": msg["timestamp"],
        },
        SENSITIVE_MSG,
    )
    chat_messages_collection.insert_one(doc)


def _decrypt_message(raw: dict) -> dict:
    d = decrypt_dict(raw, SENSITIVE_MSG)
    d.pop("encryption_metadata", None)
    d.pop("_id", None)
    ts = d.get("timestamp")
    return {
        "id": d.get("id"),
        "conversation_id": d.get("conversation_id"),
        "sender": d.get("sender"),
        "sender_name": d.get("sender_name"),
        "sender_guest_id": d.get("sender_guest_id"),
        "text": d.get("text"),
        "timestamp": ts.isoformat() if isinstance(ts, datetime) else ts,
    }


def _get_conversation_raw(conversation_id: str) -> Optional[dict]:
    return chat_collection.find_one({"_id": conversation_id})


def _decrypt_conversation(raw: dict) -> dict:
    conv_id = raw["_id"]
    d = decrypt_dict(raw, SENSITIVE_CONV)
    d.pop("encryption_metadata", None)
    d.pop("_id", None)
    participants = d.get("participants")
    if not participants:
        participants = (
            [{"guest_id": d.get("guest_id"), "name": d.get("guest_name")}]
            if d.get("guest_name")
            else []
        )
    return {
        "conversation_id": conv_id,
        "guest_id": d.get("guest_id"),
        "guest_name": d.get("guest_name"),
        "participants": participants,
        "title": d.get("title"),
        "assigned_admin_name": d.get("assigned_admin_name"),
        "status": d.get("status", "open"),
        "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"),
    }


def _get_or_create(
    conversation_id: str,
    participants: list,
    title: Optional[str],
    guest_name: Optional[str],
) -> dict:
    raw = _get_conversation_raw(conversation_id)
    if raw:
        return raw
    now = _now()
    conv = encrypt_dict(
        {
            "_id": conversation_id,
            "guest_id": participants[0]["guest_id"] if participants else None,
            "guest_name": guest_name,
            "participants": participants,
            "title": title,
            "assigned_admin_name": None,
            "status": "open",
            "created_at": now,
            "updated_at": now,
        },
        SENSITIVE_CONV,
    )
    chat_collection.insert_one(conv)
    return conv


def _messages_for(conversation_id: str) -> list:
    return [
        _decrypt_message(r)
        for r in chat_messages_collection.find({"conversation_id": conversation_id}).sort(
            "timestamp", 1
        )
    ]


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


# ── Public endpoints (no auth) ───────────────────────────────────────────────
class GuestMessageIn(BaseModel):
    message: str
    guest_id: str
    lang: str = "en"
    name: Optional[str] = None
    participants: Optional[list] = None
    title: Optional[str] = None


@router.post("/conversations/{conversation_id}/messages")
def post_guest_message(conversation_id: str, body: GuestMessageIn):
    """Guest sends a message. Bot replies unless an admin has taken over."""
    participants = body.participants or []
    if body.name and not any(
        p.get("guest_id") == body.guest_id for p in participants
    ):
        participants.append({"guest_id": body.guest_id, "name": body.name})
    if not participants:
        participants = [
            {"guest_id": body.guest_id, "name": body.name or body.guest_id}
        ]

    raw = _get_or_create(
        conversation_id, participants, body.title, body.name or participants[0]["name"]
    )

    new_messages = [
        _new_message("guest", body.message, body.name, body.guest_id)
    ]
    bot_replied = False
    if not raw.get("assigned_admin_name"):
        reply = _call_bot(body.message, body.lang)
        if reply:
            new_messages.append(_new_message("bot", reply))
            bot_replied = True

    for m in new_messages:
        _insert_message(conversation_id, m)

    chat_collection.update_one({"_id": conversation_id}, {"$set": {"updated_at": _now()}})

    conv = _decrypt_conversation(_get_conversation_raw(conversation_id))
    return {
        "conversation_id": conversation_id,
        "messages": _messages_for(conversation_id),
        "participants": conv["participants"],
        "title": conv["title"],
        "assigned_admin_name": conv["assigned_admin_name"],
        "bot_replied": bot_replied,
    }


@router.post("/conversations/{conversation_id}/participants")
def add_participant(
    conversation_id: str,
    body: dict,
):
    """Add a member to a group conversation (public)."""
    guest_id = body.get("guest_id")
    name = body.get("name")
    if not guest_id or not name:
        raise HTTPException(status_code=400, detail="guest_id and name are required")

    raw = _get_conversation_raw(conversation_id)
    if not raw:
        raise HTTPException(status_code=404, detail="Conversation not found")

    dec = decrypt_dict(raw, SENSITIVE_CONV)
    participants = dec.get("participants") or []
    if not any(p.get("guest_id") == guest_id for p in participants):
        participants.append({"guest_id": guest_id, "name": name})
    if not dec.get("guest_name") and participants:
        dec["guest_name"] = participants[0]["name"]

    dec["updated_at"] = _now()
    new_doc = encrypt_dict(dec, SENSITIVE_CONV)
    chat_collection.replace_one({"_id": conversation_id}, new_doc)

    conv = _decrypt_conversation(_get_conversation_raw(conversation_id))
    return conv


@router.get("/conversations/{conversation_id}")
def get_guest_conversation(
    conversation_id: str,
    guest_id: Optional[str] = Query(None),
):
    """Public history poll. Guest uses this to learn new messages and who
    is handling the conversation."""
    raw = _get_conversation_raw(conversation_id)
    if not raw:
        return {
            "conversation_id": conversation_id,
            "messages": [],
            "participants": [],
            "title": None,
            "assigned_admin_name": None,
        }
    conv = _decrypt_conversation(raw)
    return {**conv, "messages": _messages_for(conversation_id)}


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
    raws = list(chat_collection.find(filt).sort("updated_at", -1).limit(limit))
    result = []
    for raw in raws:
        conv = _decrypt_conversation(raw)
        count = chat_messages_collection.count_documents(
            {"conversation_id": conv["conversation_id"]}
        )
        last_raw = chat_messages_collection.find_one(
            {"conversation_id": conv["conversation_id"]}, sort=[("timestamp", -1)]
        )
        last_message = _decrypt_message(last_raw) if last_raw else None
        result.append(
            {
                **conv,
                "message_count": count,
                "last_message": last_message,
            }
        )
    return result


@router.get(
    "/admin/conversations/{conversation_id}",
    dependencies=[Depends(require_role(["ADMIN", "MANAGER"]))],
)
def admin_get_conversation(conversation_id: str):
    raw = _get_conversation_raw(conversation_id)
    if not raw:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conv = _decrypt_conversation(raw)
    return {**conv, "messages": _messages_for(conversation_id)}


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
    raw = _get_conversation_raw(conversation_id)
    if not raw:
        raise HTTPException(status_code=404, detail="Conversation not found")
    sender_name = (
        current_user.get("user_name")
        or current_user.get("name")
        or current_user.get("sub")
        or "Admin"
    )
    _insert_message(conversation_id, _new_message("admin", body.text, sender_name))
    chat_collection.update_one(
        {"_id": conversation_id},
        {"$set": {"assigned_admin_name": sender_name, "updated_at": _now()}},
    )
    conv = _decrypt_conversation(_get_conversation_raw(conversation_id))
    return {**conv, "messages": _messages_for(conversation_id)}


class AdminUpdateIn(BaseModel):
    status: Optional[str] = None
    guest_name: Optional[str] = None
    assigned_admin_name: Optional[str] = None
    title: Optional[str] = None


@router.patch(
    "/admin/conversations/{conversation_id}",
    dependencies=[Depends(require_role(["ADMIN", "MANAGER"]))],
)
def admin_update_conversation(conversation_id: str, body: AdminUpdateIn):
    raw = _get_conversation_raw(conversation_id)
    if not raw:
        raise HTTPException(status_code=404, detail="Conversation not found")
    dec = decrypt_dict(raw, SENSITIVE_CONV)
    if body.guest_name is not None:
        dec["guest_name"] = body.guest_name
    if body.title is not None:
        dec["title"] = body.title
    if body.assigned_admin_name is not None:
        dec["assigned_admin_name"] = body.assigned_admin_name
    if body.status is not None:
        dec["status"] = body.status
    dec["updated_at"] = _now()
    new_doc = encrypt_dict(dec, SENSITIVE_CONV)
    chat_collection.replace_one({"_id": conversation_id}, new_doc)
    conv = _decrypt_conversation(_get_conversation_raw(conversation_id))
    return {**conv, "messages": _messages_for(conversation_id)}
