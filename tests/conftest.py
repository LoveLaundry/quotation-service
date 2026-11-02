"""Shared harness for quotation-service tests.

No real MongoDB instance is required — and, more importantly, none is allowed.
A ``mongomock`` database stands in for the MAIN collections, patched onto the
database modules BEFORE the repository/router modules are imported, so their
module-level bindings (``from .database.main_db import quotations_collection``)
resolve to the mock.
"""
import importlib
import os

import pytest
from mongomock import MongoClient

# Must be set before quotation_service.config is imported: DB_TYPE is derived
# from DATABASE_URL at import time and these tests exercise the MongoDB backend.
os.environ.setdefault("DATABASE_URL", "mongodb://localhost:27017")
# Pin every Mongo role at localhost. ``auth_helper`` calls load_dotenv() at
# import time, so without these the .env Atlas URI leaks into os.environ and
# pymongo resolves the production cluster's SRV record during import — which
# both hangs on DNS offline and risks writing to live data online.
for _k in ("MONGODB_MAIN_URI", "MONGODB_SECONDARY_URI", "MONGODB_LOCAL_URI"):
    os.environ.setdefault(_k, "mongodb://localhost:27017")
os.environ.setdefault("JWT_SECRET", "test-secret-not-a-real-credential")
os.environ.setdefault("MASTER_KEY", "test-master-key-not-real")
os.environ.setdefault("SYNC_ENABLED", "false")

MOCK_DB_NAME = "mock_quotation"

COLLECTION_NAMES = [
    "raw_quotations_collection",
    "sync_queue_collection",
    "sync_status_collection",
    "sync_logs_collection",
    "chat_collection",
    "chat_messages_collection",
    "hotels_collection",
    "shops_collection",
]

# Modules whose module-level collection bindings must point at the mock.
# Anything doing ``from ..database.main_db import <collection>`` caches the
# handle at import time, so it must be reloaded after patching.
DEPENDENT_MODULES = [
    "quotation_service.mongodb_repository",
    "quotation_service.routers.chat",
    "quotation_service.routers.admin_database",
    "quotation_service.services.synchronization_service",
    "quotation_service.services.local_sync_service",
    "quotation_service.services.verification_service",
    "quotation_service.repositories.main_repository",
    "quotation_service.main",
]

# (module, attribute) pairs that must all resolve to the mock database.
GUARDED_HANDLES = [
    ("quotation_service.database.main_db", "raw_quotations_collection"),
    ("quotation_service.mongodb_repository", "quotations_collection"),
    ("quotation_service.routers.chat", "chat_collection"),
    ("quotation_service.routers.chat", "chat_messages_collection"),
]


@pytest.fixture(scope="session")
def mock_client():
    return MongoClient()


@pytest.fixture(scope="session", autouse=True)
def mocked_db(mock_client):
    """Swap every database collection for a mongomock stand-in."""
    db = mock_client[MOCK_DB_NAME]

    import quotation_service.database.main_db as main_db

    for name in COLLECTION_NAMES:
        setattr(main_db, name, db.get_collection(name))

    # The tenant-scoped wrapper caches the raw collection at import time.
    from quotation_service.tenant_scope import TenantScopedCollection

    main_db.quotations_collection = TenantScopedCollection(
        db.get_collection("quotations"), "quotations"
    )

    for module_name in DEPENDENT_MODULES:
        try:
            importlib.reload(importlib.import_module(module_name))
        except Exception:  # pragma: no cover - optional modules
            pass

    _assert_no_live_clients()
    return db


def _assert_no_live_clients() -> None:
    """Fail loudly if any module still holds a real MongoDB collection.

    A stale handle points at whatever ``.env`` names, which in development is
    the PRODUCTION Atlas cluster — a stray write in a test would mutate live
    data. Assert every cached handle belongs to the mongomock database.
    """
    prefix = f"{MOCK_DB_NAME}."
    leaked = []
    for module_name, attr in GUARDED_HANDLES:
        module = importlib.import_module(module_name)
        collection = getattr(module, attr, None)
        full_name = getattr(collection, "full_name", None)
        if full_name is None:
            # TenantScopedCollection wraps the raw handle.
            full_name = getattr(getattr(collection, "_collection", None), "full_name", None)
        if full_name is None or not str(full_name).startswith(prefix):
            leaked.append(f"{module_name}.{attr} -> {full_name}")
    if leaked:
        raise AssertionError(
            "Test harness leaked a live MongoDB handle:\n  " + "\n  ".join(leaked)
        )
