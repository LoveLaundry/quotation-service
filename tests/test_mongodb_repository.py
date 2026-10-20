import os
from datetime import datetime, timezone

os.environ.setdefault("MASTER_KEY", "test-only-master-key-0123456789abcdef")
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-0123456789abcdef")
os.environ.setdefault("DATABASE_URL", "mongodb://127.0.0.1:27017")

import mongomock

from quotation_service import mongodb_repository
from quotation_service.mongodb_repository import MongoDBQuotationRepository


def test_create_persists_initial_status_history(monkeypatch):
    repo = MongoDBQuotationRepository.__new__(MongoDBQuotationRepository)
    repo.collection = mongomock.MongoClient().get_database("test")["quotations"]
    monkeypatch.setattr(mongodb_repository, "bump_version", lambda *_args: 1)
    monkeypatch.setattr(mongodb_repository, "enqueue_sync", lambda *_args: None)
    monkeypatch.setattr(
        mongodb_repository, "attach_verification_to", lambda _entity, _id, result: result
    )
    status_history = [
        {
            "status": "draft",
            "changed_at": datetime.now(timezone.utc).isoformat(),
            "changed_by": "operator",
            "note": "Created",
        }
    ]

    created = repo.create(
        {
            "client_name": "Test Client",
            "status": "draft",
            "status_history": status_history,
        }
    )

    assert created["status_history"] == status_history
