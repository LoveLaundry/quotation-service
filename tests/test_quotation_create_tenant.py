import os

os.environ.setdefault("MASTER_KEY", "test-only-master-key-0123456789abcdef")
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-0123456789abcdef")
os.environ.setdefault("DATABASE_URL", "mongodb://127.0.0.1:27017")

import mongomock
import pytest
from fastapi.testclient import TestClient

from quotation_service import mongodb_repository, repository_factory
from quotation_service.auth_helper import create_access_token
from quotation_service.main import app
from quotation_service.tenant_scope import TenantScopedCollection


@pytest.fixture
def quotation_client(monkeypatch):
    raw = mongomock.MongoClient().get_database("test")["quotations"]
    repo = mongodb_repository.MongoDBQuotationRepository.__new__(
        mongodb_repository.MongoDBQuotationRepository
    )
    repo.collection = TenantScopedCollection(raw, "quotations")
    monkeypatch.setattr(repository_factory, "_mongodb_repo", repo)
    monkeypatch.setattr(mongodb_repository, "bump_version", lambda *_args: 1)
    monkeypatch.setattr(mongodb_repository, "enqueue_sync", lambda *_args: None)
    monkeypatch.setattr(
        mongodb_repository, "attach_verification_to", lambda _entity, _id, result: result
    )
    return TestClient(app), raw


def _token(role="ADMIN", status="ACTIVE", laundry_id="laundry-a"):
    return create_access_token(
        {
            "sub": "test-user",
            "role": role,
            "laundry_id": laundry_id,
            "laundries": [{"id": laundry_id, "status": status}],
        }
    )


def test_quotation_create_requires_bearer_jwt_and_authorized_role(quotation_client):
    client, raw = quotation_client
    payload = {"client_name": "Test Client", "status": "draft"}

    assert client.post("/quotations", json=payload).status_code == 401
    response = client.post(
        "/quotations",
        json=payload,
        headers={"Authorization": f"Bearer {_token(role='STAFF')}"},
    )
    assert response.status_code == 403
    assert raw.count_documents({}) == 0


def test_quotation_create_stamps_active_signed_tenant_and_history(quotation_client):
    client, raw = quotation_client
    response = client.post(
        "/quotations",
        json={"client_name": "Test Client", "status": "draft"},
        headers={
            "Authorization": f"Bearer {_token(role='MANAGER')}",
            "X-API-Key": "not-required",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status_history"][0]["status"] == "draft"
    assert body["status_history"][0]["note"] == "Created"
    stored = raw.find_one({})
    assert stored["laundry_id"] == "laundry-a"
    assert stored["status_history"] == body["status_history"]

    read_response = client.get(
        f"/quotations/{body['id']}",
        headers={"Authorization": f"Bearer {_token(role='MANAGER')}"},
    )
    assert read_response.status_code == 200, read_response.text
    assert read_response.json()["status_history"] == body["status_history"]

    foreign_response = client.get(
        f"/quotations/{body['id']}",
        headers={
            "Authorization": (
                "Bearer " + _token(role="MANAGER", laundry_id="laundry-b")
            )
        },
    )
    assert foreign_response.status_code == 404


def test_quotation_create_rejects_inactive_tenant_before_writing(quotation_client):
    client, raw = quotation_client
    response = client.post(
        "/quotations",
        json={"client_name": "Test Client", "status": "draft"},
        headers={
            "Authorization": (
                "Bearer " + _token(status="SUSPENDED", laundry_id="laundry-a")
            )
        },
    )

    assert response.status_code == 403
    assert raw.count_documents({}) == 0
