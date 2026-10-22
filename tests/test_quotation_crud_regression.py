"""Regression tests for the quotation CRUD path.

The service reaches MongoDB through plain ``pymongo`` (not motor), but the
tenant guard (``TenantScopedCollection``) was once declared with ``async def``
methods. Every scoped call then returned an un-awaited coroutine: reads looked
empty and writes silently no-oped, which surfaced as "Failed to decrypt
quotation: argument of type 'coroutine' is not iterable".

These tests pin:
  * the guard returns real pymongo results (synchronous contract),
  * the full create -> read -> update -> delete round trip works and stores
    encrypted values,
  * the update payload never carries the immutable / guard-owned fields,
  * documents of another laundry stay invisible,
  * the system-level MAIN handle stays unscoped so the background sync worker
    (which has no request claims) can still run.
"""
import os
from contextlib import contextmanager

# Must be set before the service package is imported: crypto_helper refuses to
# start without a MASTER_KEY, and auth_helper without a JWT_SECRET.
os.environ.setdefault("MASTER_KEY", "test-only-master-key-0123456789abcdef")
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-0123456789abcdef")
os.environ.setdefault("DATABASE_URL", "mongodb://127.0.0.1:27017")

import mongomock
import pytest
from bson import ObjectId

from quotation_service import mongodb_repository
from quotation_service.crypto_helper import encrypt_dict
from quotation_service.tenant_scope import (
    TenantScopedCollection,
    reset_request_claims,
    set_request_claims,
)

SENSITIVE_FIELDS = ["client_name", "quotation_title", "line_items"]


@contextmanager
def as_tenant(laundry_id: str):
    """Bind request claims for one laundry, exactly like the auth middleware."""
    token = set_request_claims(
        {
            "laundry_id": laundry_id,
            "laundries": [{"id": laundry_id, "status": "ACTIVE"}],
        }
    )
    try:
        yield
    finally:
        reset_request_claims(token)


class SpyCollection:
    """Records the update payloads that reach the driver."""

    def __init__(self, raw):
        self._raw = raw
        self.payloads: list[dict] = []

    def find_one_and_update(self, filt, update, *args, **kwargs):
        self.payloads.append(update)
        return self._raw.find_one_and_update(filt, update, *args, **kwargs)

    def __getattr__(self, item):
        return getattr(self._raw, item)


@pytest.fixture
def quotations(monkeypatch):
    """A mongomock-backed quotations collection plus a repository using it."""
    raw = mongomock.MongoClient().get_database("test").get_collection("quotations")
    spy = SpyCollection(raw)

    repo = mongodb_repository.MongoDBQuotationRepository.__new__(
        mongodb_repository.MongoDBQuotationRepository
    )
    repo.collection = TenantScopedCollection(spy, "quotations")

    # The sync bookkeeping talks to the MAIN database and the verification
    # service to the sync_status collection; both are outside this test.
    monkeypatch.setattr(mongodb_repository, "bump_version", lambda *a, **k: 1)
    monkeypatch.setattr(mongodb_repository, "enqueue_sync", lambda *a, **k: None)
    monkeypatch.setattr(mongodb_repository, "enqueue_delete", lambda *a, **k: None)
    monkeypatch.setattr(
        mongodb_repository, "attach_verification_to", lambda _e, _i, doc: doc
    )
    monkeypatch.setattr(
        mongodb_repository, "attach_verifications_bulk", lambda _e, docs: docs
    )
    return {"repo": repo, "raw": raw, "spy": spy}


def _payload():
    return {
        "client_name": "Ada Lovelace",
        "quotation_title": "Winter coats",
        "line_items": [{"item_name": "Coat", "price": 100}],
        "status": "draft",
        "tag": "shop",
    }


def test_tenant_guard_returns_real_pymongo_results(quotations):
    """The guard must be synchronous — a coroutine here breaks every read."""
    raw = quotations["raw"]
    raw.insert_one(encrypt_dict(dict(_payload(), _id=ObjectId()), SENSITIVE_FIELDS))

    scoped = TenantScopedCollection(raw, "quotations")
    with as_tenant("lovelaundry"):
        doc = scoped.find_one({})
        assert not hasattr(doc, "__await__"), "scoped read returned a coroutine"
        assert isinstance(doc, dict)

        cursor = scoped.find({})
        assert not hasattr(cursor, "__await__"), "scoped find returned a coroutine"
        assert len(list(cursor)) == 1

        result = scoped.insert_one(encrypt_dict(dict(_payload()), SENSITIVE_FIELDS))
        assert not hasattr(result, "__await__"), "scoped write returned a coroutine"
        assert result.inserted_id is not None


def test_create_read_update_delete_round_trip(quotations):
    """The end-to-end path that used to fail with 'Failed to decrypt quotation'."""
    repo = quotations["repo"]
    raw = quotations["raw"]

    with as_tenant("lovelaundry"):
        created = repo.create(_payload())
        assert created is not None
        assert created["client_name"] == "Ada Lovelace"
        qid = created["id"]

        stored = raw.find_one({"_id": ObjectId(qid)})
        assert stored is not None, "create did not persist a document"
        # Sensitive fields must be encrypted at rest, not stored as plaintext.
        assert isinstance(stored["client_name"], dict)
        assert "ciphertext" in stored["client_name"]
        assert stored["laundry_id"] == "lovelaundry"

        fetched = repo.get_by_id(qid)
        assert fetched is not None
        assert fetched["client_name"] == "Ada Lovelace"
        assert fetched["quotation_title"] == "Winter coats"

        listed = repo.get_all()
        assert [q["id"] for q in listed] == [qid]

        updated = repo.update(qid, {"status": "ready"})
        assert updated is not None
        assert updated["status"] == "ready"
        assert updated["client_name"] == "Ada Lovelace"
        assert raw.find_one({"_id": ObjectId(qid)})["status"] == "ready"

        assert repo.delete(qid) is True
        assert repo.get_by_id(qid) is None
        assert raw.find_one({"_id": ObjectId(qid)}) is None


def test_update_payload_excludes_immutable_and_guard_owned_fields(quotations):
    """`_id` is immutable in MongoDB; `laundry_id`/`sync_version` are not ours."""
    repo = quotations["repo"]
    spy = quotations["spy"]

    with as_tenant("lovelaundry"):
        created = repo.create(_payload())
        qid = created["id"]
        spy.payloads.clear()

        assert repo.update(qid, {"status": "ready"}) is not None

        assert spy.payloads, "update did not reach the driver"
        payload = spy.payloads[-1]
        assert "$set" in payload
        assert "_id" not in payload["$set"]
        assert "laundry_id" not in payload["$set"]
        assert "sync_version" not in payload["$set"]
        assert payload["$set"]["status"] == "ready"


def test_documents_of_another_laundry_stay_invisible(quotations):
    """Tenant isolation: a foreign record is neither listed nor readable."""
    repo = quotations["repo"]
    raw = quotations["raw"]

    with as_tenant("otherlaundry"):
        foreign = repo.create(_payload())
    with as_tenant("lovelaundry"):
        mine = repo.create(dict(_payload(), client_name="Mine"))

    assert raw.count_documents({}) == 2

    with as_tenant("lovelaundry"):
        assert [q["id"] for q in repo.get_all()] == [mine["id"]]
        assert repo.get_by_id(foreign["id"]) is None
        assert [q["id"] for q in repo.get_by_tag("shop")] == [mine["id"]]

    with as_tenant("otherlaundry"):
        assert [q["id"] for q in repo.get_all()] == [foreign["id"]]
        assert repo.get_by_id(mine["id"]) is None


def test_legacy_unstamped_documents_belong_to_lovelaundry(quotations):
    """Pre-multi-tenancy documents carry no laundry_id and are LoveLaundry's."""
    repo = quotations["repo"]
    raw = quotations["raw"]
    legacy_id = str(raw.insert_one(encrypt_dict(dict(_payload()), SENSITIVE_FIELDS)).inserted_id)

    with as_tenant("lovelaundry"):
        assert repo.get_by_id(legacy_id) is not None
        assert len(repo.get_all()) == 1

    with as_tenant("otherlaundry"):
        assert repo.get_by_id(legacy_id) is None
        assert repo.get_all() == []


def test_main_handle_is_unscoped_for_the_background_worker():
    """The sync worker runs without request claims and must still reach MAIN."""
    from quotation_service.repositories import entity_registry

    main_collection = entity_registry.get_main_collection("quotation")
    assert not isinstance(main_collection, TenantScopedCollection)
    # The business-facing handle, by contrast, must stay guarded.
    from quotation_service.database import main_db

    assert isinstance(main_db.quotations_collection, TenantScopedCollection)
