"""
MAIN database — production source of truth.

All normal application reads and writes MUST go through these
collections. Never read or write the Secondary/Local databases
from business logic.
"""
from pymongo.collection import Collection

from .connection_manager import ROLE_MAIN, get_database

_db = get_database(ROLE_MAIN)

quotations_collection: Collection = _db.get_collection("quotations")

# Sync infrastructure collections live alongside business data in MAIN.
sync_status_collection: Collection = _db.get_collection("sync_status")
sync_queue_collection: Collection = _db.get_collection("sync_queue")
sync_logs_collection: Collection = _db.get_collection("sync_logs")

# Public chatbot conversations (guest <-> bot/admin).
chat_collection: Collection = _db.get_collection("chat_conversations")
chat_messages_collection: Collection = _db.get_collection("chat_messages")


def ensure_indexes():
    """Create all required indexes on the MAIN database."""
    # Quotations indexes
    quotations_collection.create_index("client_name_search")
    quotations_collection.create_index("created_at")
    quotations_collection.create_index("tag")

    # Sync infrastructure indexes
    sync_status_collection.create_index([("entity", 1), ("record_id", 1)], unique=True)
    sync_queue_collection.create_index([("status", 1), ("next_attempt_at", 1)])
    sync_queue_collection.create_index([("entity", 1), ("record_id", 1)], unique=True)
    sync_logs_collection.create_index([("operation", 1), ("started_at", -1)])

    # Chat conversations indexes
    chat_collection.create_index([("updated_at", -1)])
    chat_collection.create_index("guest_id")
    chat_messages_collection.create_index([("conversation_id", 1), ("timestamp", 1)])