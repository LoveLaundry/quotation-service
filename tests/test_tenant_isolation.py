"""Tenant isolation for the quotation-service.

Mirrors the bill-service / laundry-management contract: a session may only
touch its own laundry's records; unknown and foreign ids both surface as 404
(no probing oracle); listings exclude foreign rows; creates stamp the session
laundry; and pre-tenancy documents without ``laundry_id`` read as LoveLaundry's
(the sole historical tenant). Guest/public tag lookups stay deliberately
unscoped (possession of the unguessable code is the authorization).
"""
import os

# Must be set before the service package is imported: crypto_helper refuses
# to start without MASTER_KEY, and auth_helper without JWT_SECRET.
os.environ.setdefault("MASTER_KEY", "test-only-master-key-0123456789abcdef")
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-0123456789abcdef")
os.environ.setdefault("DATABASE_URL", "mongodb://127.0.0.1:27017")

import mongomock
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from quotation_service import mongodb_repository as repo_mod
from quotation_service.mongodb_repository import MongoDBQuotationRepository
from quotation_service.tenancy import (
    LOVE_LAUNDRY_ID,
    assert_same_laundry,
    laundry_match,
    owned_filter,
    resolve_laundry_id,
    scoped_query,
)

LAUNDRY_A = "acme"
LAUNDRY_B = "other"


def tenant_user(laundry_id=LAUNDRY_A, role="ADMIN", status="ACTIVE"):
    return {
        "auth_id": "tester",
        "user_id": "tester",
        "username": "tester",
        "role": role,
        "laundry_id": laundry_id,
        "laundries": [{"id": laundry_id, "role": role, "status": status}],
    }


# ── tenancy helpers (pure) ────────────────────────────────────────────────


def test_resolve_laundry_fails_closed():
    with pytest.raises(HTTPException) as err:
        resolve_laundry_id(None)
    assert err.value.status_code == 401
    with pytest.raises(HTTPException) as err:
        resolve_laundry_id({"role": "ADMIN", "laundries": []})
    assert err.value.status_code == 400
    with pytest.raises(HTTPException) as err:
        resolve_laundry_id(
            {
                "role": "ADMIN",
                "laundry_id": LAUNDRY_A,
                "laundries": [{"id": LAUNDRY_B, "status": "ACTIVE"}],
            }
        )
    assert err.value.status_code == 403
    with pytest.raises(HTTPException) as err:
        resolve_laundry_id(
            {
                "role": "ADMIN",
                "laundry_id": LAUNDRY_A,
                "laundries": [{"id": LAUNDRY_A, "status": "SUSPENDED"}],
            }
        )
    assert err.value.status_code == 403
    assert resolve_laundry_id(tenant_user()) == LAUNDRY_A


def test_platform_support_token_needs_and_returns_laundry():
    platform = {
        "role": "PLATFORM_ADMIN",
        "laundry_id": LAUNDRY_B,
        "laundries": [],
        "platform_admin": True,
    }
    assert resolve_laundry_id(platform) == LAUNDRY_B
    with pytest.raises(HTTPException) as err:
        resolve_laundry_id({"role": "PLATFORM_ADMIN", "platform_admin": True})
    assert err.value.status_code == 400


def test_love_laundry_sees_legacy_unstamped_documents():
    scoped = scoped_query({"kind": "quote"}, LOVE_LAUNDRY_ID)
    tenant_clause = scoped["$and"][1]["$or"]
    assert {"laundry_id": LOVE_LAUNDRY_ID} in tenant_clause
    assert {"laundry_id": {"$exists": False}} in tenant_clause
    assert {"laundry_id": None} in tenant_clause
    # A foreign tenant never matches an unstamped (pre-tenancy) document.
    assert scoped_query({"kind": "quote"}, LAUNDRY_B) == {
        "kind": "quote",
        "laundry_id": LAUNDRY_B,
    }
    # Empty business query collapses to the tenant clause alone.
    assert scoped_query({}, LOVE_LAUNDRY_ID)["$or"]
    assert laundry_match(LAUNDRY_A) == {"laundry_id": LAUNDRY_A}
    assert owned_filter("id-1", LAUNDRY_A) == {"_id": "id-1", "laundry_id": LAUNDRY_A}


def test_assert_same_laundry_404s_across_tenants():
    with pytest.raises(HTTPException) as err:
        assert_same_laundry({"laundry_id": LAUNDRY_B}, LAUNDRY_A, "Quotation")
    assert err.value.status_code == 404
    with pytest.raises(HTTPException) as err:
        assert_same_laundry(None, LAUNDRY_A, "Quotation")
    assert err.value.status_code == 404
    # Legacy document belongs to LoveLaundry only.
    assert_same_laundry({}, LOVE_LAUNDRY_ID, "Quotation")
    assert_same_laundry({"laundry_id": LAUNDRY_A}, LAUNDRY_A, "Quotation")


# ── repository scoping (in-memory Mongo) ──────────────────────────────────


@pytest.fixture
def repo(monkeypatch):
    client = mongomock.MongoClient()
    coll = client.get_database("test").get_collection("quotations")
    monkeypatch.setattr(repo_mod, "quotations_collection", coll)
    # The repository's write path normally touches the sync/verification
    # infrastructure; stub it so the unit test stays in-memory.
    monkeypatch.setattr(repo_mod, "bump_version", lambda *a, **k: 1)
    monkeypatch.setattr(repo_mod, "enqueue_sync", lambda *a, **k: None)
    monkeypatch.setattr(repo_mod, "enqueue_delete", lambda *a, **k: None)
    monkeypatch.setattr(repo_mod, "attach_verification_to", lambda e, i, s: s)
    monkeypatch.setattr(repo_mod, "attach_verifications_bulk", lambda e, s: s)
    return MongoDBQuotationRepository()


def _create(repo, laundry_id, title="Quote"):
    return repo.create(
        {"client_name": "Client", "quotation_title": title, "line_items": [], "tag": "shop"},
        laundry_id=laundry_id,
    )


def test_create_stamps_the_session_laundry(repo):
    created = _create(repo, LAUNDRY_A)
    assert created["laundry_id"] == LAUNDRY_A
    raw = repo.collection.find_one()
    assert raw["laundry_id"] == LAUNDRY_A


def test_list_is_scoped_to_the_session_laundry(repo):
    _create(repo, LAUNDRY_A, "A")
    _create(repo, LAUNDRY_B, "B")
    legacy = _create(repo, None, "legacy")

    assert {q["quotation_title"] for q in repo.get_all(laundry_id=LAUNDRY_A)} == {"A"}
    assert {q["quotation_title"] for q in repo.get_all(laundry_id=LAUNDRY_B)} == {"B"}
    # Unstamped (pre-tenancy) docs are visible to LoveLaundry and to the
    # unscoped system callers, never to a foreign tenant.
    assert {q["quotation_title"] for q in repo.get_all(laundry_id=LOVE_LAUNDRY_ID)} == {"legacy"}
    assert repo.get_by_id(legacy["id"], laundry_id=LAUNDRY_B) is None
    assert repo.get_by_id(legacy["id"], laundry_id=LOVE_LAUNDRY_ID)["quotation_title"] == "legacy"
    assert repo.get_all(laundry_id=None)  # unscoped system view
    assert len(repo.get_all(laundry_id=None)) == 3


def test_cross_tenant_get_update_delete_return_not_found(repo):
    foreign = _create(repo, LAUNDRY_B, "B")

    assert repo.get_by_id(foreign["id"], laundry_id=LAUNDRY_A) is None
    assert repo.update(foreign["id"], {"quotation_title": "hacked"}, laundry_id=LAUNDRY_A) is None
    assert repo.delete(foreign["id"], laundry_id=LAUNDRY_A) is False

    # The foreign document is untouched.
    survivor = repo.get_by_id(foreign["id"], laundry_id=LAUNDRY_B)
    assert survivor is not None
    assert survivor["quotation_title"] == "B"
    # Unscoped system callers still see everything.
    assert repo.get_by_id(foreign["id"]) is not None


def test_same_tenant_update_and_delete_work(repo):
    own = _create(repo, LAUNDRY_A, "A")
    updated = repo.update(own["id"], {"quotation_title": "A2"}, laundry_id=LAUNDRY_A)
    assert updated["quotation_title"] == "A2"
    assert repo.delete(own["id"], laundry_id=LAUNDRY_A) is True
    assert repo.get_by_id(own["id"], laundry_id=LAUNDRY_A) is None


def test_tags_are_scoped_and_stamped(repo):
    qa = _create(repo, LAUNDRY_A, "A")
    qb = _create(repo, LAUNDRY_B, "B")

    # Cannot mint tags for a foreign quotation.
    assert repo.create_tags(qb["id"], 1, False, "x", laundry_id=LAUNDRY_A) is None

    tags = repo.create_tags(qa["id"], 2, False, "label", laundry_id=LAUNDRY_A)
    assert len(tags) == 2
    assert all(t["laundry_id"] == LAUNDRY_A for t in tags)

    assert len(repo.list_tags(qa["id"], laundry_id=LAUNDRY_A)) == 2
    assert repo.list_tags(qa["id"], laundry_id=LAUNDRY_B) == []

    code = tags[0]["code"]
    assert repo.get_tag(code, laundry_id=LAUNDRY_A)["code"] == code
    # Public (unscoped) possession-based lookup still resolves the tag.
    assert repo.get_tag(code, laundry_id=None)["code"] == code


# ── endpoint-level isolation (FastAPI TestClient) ─────────────────────────


@pytest.fixture
def api(monkeypatch):
    client = mongomock.MongoClient()
    coll = client.get_database("test").get_collection("quotations")
    monkeypatch.setattr(repo_mod, "quotations_collection", coll)
    monkeypatch.setattr(repo_mod, "bump_version", lambda *a, **k: 1)
    monkeypatch.setattr(repo_mod, "enqueue_sync", lambda *a, **k: None)
    monkeypatch.setattr(repo_mod, "enqueue_delete", lambda *a, **k: None)
    monkeypatch.setattr(repo_mod, "attach_verification_to", lambda e, i, s: s)
    monkeypatch.setattr(repo_mod, "attach_verifications_bulk", lambda e, s: s)

    repo = MongoDBQuotationRepository()

    from quotation_service import main as main_mod

    main_mod.app.dependency_overrides[main_mod.get_repository] = lambda: repo
    main_mod.app.dependency_overrides[main_mod.get_current_user] = lambda: tenant_user(LAUNDRY_A)
    test_client = TestClient(main_mod.app)
    try:
        yield test_client, repo
    finally:
        main_mod.app.dependency_overrides.clear()


def test_endpoint_cross_tenant_is_404_and_guest_tag_lookup_works(api):
    client, repo = api
    own = _create(repo, LAUNDRY_A, "own")
    foreign = _create(repo, LAUNDRY_B, "foreign")

    # Own record is readable; foreign is a 404 (no probing oracle).
    assert client.get(f"/quotations/{own['id']}").status_code == 200
    assert client.get(f"/quotations/{foreign['id']}").status_code == 404
    assert client.put(f"/quotations/{foreign['id']}", json={"quotation_title": "x"}).status_code == 404
    assert client.delete(f"/quotations/{foreign['id']}").status_code == 404

    # The foreign record survives.
    assert repo.get_by_id(foreign["id"], laundry_id=LAUNDRY_B) is not None

    # Listing only returns the session laundry's quotations.
    listing = client.get("/quotations")
    assert listing.status_code == 200
    assert {q["quotation_title"] for q in listing.json()} == {"own"}

    # Guest/public tag lookup is deliberately unscoped and still works.
    tags = repo.create_tags(own["id"], 1, False, "bag", laundry_id=LAUNDRY_A)
    code = tags[0]["code"]
    guest = client.get(f"/tags/{code}")
    assert guest.status_code == 200
    assert guest.json()["quotation_id"] == own["id"]


def test_endpoint_guest_shop_route_is_unscoped(api):
    client, repo = api
    _create(repo, LAUNDRY_B, "shop-foreign")
    r = client.get("/quotations/guest/shop")
    assert r.status_code == 200
    assert {q["quotation_title"] for q in r.json()} == {"shop-foreign"}
