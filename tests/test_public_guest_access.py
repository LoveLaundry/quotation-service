"""Guest-facing endpoints must keep working under the fail-closed tenant guard.

Guests have no session, so these four routes are capability-based: the
unguessable quotation id / tag code is the credential. They must read through
the explicit ``raw`` escape hatch, NOT the scoped collection (which raises 401
without claims). The public storefront listing is the exception: it is limited
to the configured public laundry so other tenants' shop quotations never leak.
"""
import os
from datetime import datetime, timezone

# Must be set before the service package is imported: crypto_helper refuses to
# start without a MASTER_KEY, and auth_helper without a JWT_SECRET.
os.environ.setdefault("MASTER_KEY", "test-only-master-key-0123456789abcdef")
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-0123456789abcdef")
os.environ.setdefault("DATABASE_URL", "mongodb://127.0.0.1:27017")

import mongomock
import pytest
from fastapi.testclient import TestClient

from quotation_service import mongodb_repository, repository_factory
from quotation_service.crypto_helper import encrypt_dict
from quotation_service.main import app
from quotation_service.tenant_scope import TenantScopedCollection

SENSITIVE_FIELDS = ["client_name", "quotation_title", "line_items"]


def _quotation(client_name, title, tag, laundry_id):
    doc = {
        "client_name": client_name,
        "quotation_title": title,
        "line_items": [],
        "status": "ready",
        "status_history": [],
        "tag": tag,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    encrypted = encrypt_dict(doc, SENSITIVE_FIELDS)
    if laundry_id is not None:
        encrypted["laundry_id"] = laundry_id
    return encrypted


@pytest.fixture
def guest(monkeypatch):
    raw = mongomock.MongoClient().get_database("test")["quotations"]
    public_q = raw.insert_one(_quotation("Public Guest", "Public order", "shop", "lovelaundry"))
    legacy_q = raw.insert_one(_quotation("Legacy Guest", "Legacy order", "shop", None))
    foreign_q = raw.insert_one(_quotation("Foreign Guest", "Foreign order", "shop", "otherlaundry"))

    tags = raw.database["garment_tags"]
    tags.insert_one(
        {
            "code": "tag-public",
            "quotation_id": str(public_q.inserted_id),
            "line_item_id": None,
            "label": "Shirt",
        }
    )
    tags.insert_one(
        {
            "code": "tag-foreign",
            "quotation_id": str(foreign_q.inserted_id),
            "line_item_id": None,
            "label": "Trouser",
        }
    )

    repo = mongodb_repository.MongoDBQuotationRepository.__new__(
        mongodb_repository.MongoDBQuotationRepository
    )
    repo.collection = TenantScopedCollection(raw, "quotations")
    monkeypatch.setattr(repository_factory, "_mongodb_repo", repo)
    monkeypatch.setattr(
        mongodb_repository,
        "attach_verifications_bulk",
        lambda _entity, results: results,
    )
    return TestClient(app), str(public_q.inserted_id), str(foreign_q.inserted_id)


def test_guest_shop_shows_only_public_laundry(guest):
    client, _, _ = guest
    r = client.get("/quotations/guest/shop")
    assert r.status_code == 200, r.text
    names = {q["client_name"] for q in r.json()}
    assert names == {"Public Guest", "Legacy Guest"}


def test_tracking_works_without_a_session(guest):
    client, public_id, foreign_id = guest
    r = client.get(f"/quotations/{public_id}/tracking")
    assert r.status_code == 200, r.text
    assert r.json()["quotation_title"] == "Public order"

    # Capability-based: a tag printed for another laundry still resolves.
    r = client.get(f"/quotations/{foreign_id}/tracking")
    assert r.status_code == 200, r.text
    assert r.json()["quotation_title"] == "Foreign order"


def test_tag_lookup_and_tracking_work_without_a_session(guest):
    client, public_id, _ = guest
    r = client.get("/tags/tag-public")
    assert r.status_code == 200, r.text
    assert r.json()["quotation_id"] == public_id
    assert r.json()["client_first_name"] == "Public"

    r = client.get("/tags/tag-public/tracking")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ready"

    assert client.get("/tags/unknown-code").status_code == 404
