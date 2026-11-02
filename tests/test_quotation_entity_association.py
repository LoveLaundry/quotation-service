"""Quotations bound to a hotel or shop entity.

A quotation used to carry only the ``client_name`` string, which cannot tell two
customers with the same name apart and cannot be validated against a tenant.
``architecture/MULTI_TENANT_ENTITY_ARCHITECTURE.md`` requires the entity
reference, scoped to the same laundry, with cross-tenant linkage rejected before
any write.
"""
import mongomock
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from quotation_service import mongodb_repository, repository_factory
from quotation_service.auth_helper import create_access_token
from quotation_service.main import app
from quotation_service.tenancy import validate_entity_association
from quotation_service.tenant_scope import TenantScopedCollection

LAUNDRY = "laundry-a"
OTHER = "laundry-b"


@pytest.fixture
def quots(monkeypatch):
    """Isolated quotation repo plus mock registries."""
    raw = mongomock.MongoClient().get_database("test")["quotations"]
    repo = mongodb_repository.MongoDBQuotationRepository.__new__(
        mongodb_repository.MongoDBQuotationRepository
    )
    repo.collection = TenantScopedCollection(raw, "quotations")
    monkeypatch.setattr(repository_factory, "_mongodb_repo", repo)
    monkeypatch.setattr(mongodb_repository, "bump_version", lambda *_a: 1)
    monkeypatch.setattr(mongodb_repository, "enqueue_sync", lambda *_a: None)
    monkeypatch.setattr(
        mongodb_repository, "attach_verification_to", lambda _e, _i, result: result
    )
    return TestClient(app), raw


def _token(laundry_id=LAUNDRY, role="ADMIN"):
    return create_access_token(
        {
            "sub": "test-user",
            "role": role,
            "laundry_id": laundry_id,
            "laundries": [{"id": laundry_id, "status": "ACTIVE"}],
        }
    )


@pytest.fixture
def registries(monkeypatch):
    """Patch the entity registries with mock collections."""
    import quotation_service.database.main_db as main_db

    hotels = mongomock.MongoClient().get_database("test")["hotels"]
    shops = mongomock.MongoClient().get_database("test")["shops"]
    monkeypatch.setattr(main_db, "hotels_collection", hotels)
    monkeypatch.setattr(main_db, "shops_collection", shops)
    return hotels, shops


def _seed(collection, field, entity_id, *, laundry=LAUNDRY, name=None, status="ACTIVE"):
    collection.insert_one(
        {
            field: entity_id,
            "name": name or entity_id.replace("-", " ").title(),
            "associated_laundry_id": laundry,
            "status": status,
        }
    )


# ── the validator in isolation ────────────────────────────────────────────────


def test_no_entity_returns_empty_for_walk_in(registries):
    assert (
        validate_entity_association(
            tag="shop", hotel_id=None, shop_id=None, laundry_id=LAUNDRY
        )
        == {}
    )


def test_tag_and_entity_side_must_agree(registries):
    hotels, shops = registries
    _seed(shops, "shop_id", "city-express")

    with pytest.raises(HTTPException) as err:
        validate_entity_association(
            tag="hotel", hotel_id=None, shop_id="city-express", laundry_id=LAUNDRY
        )
    assert err.value.status_code == 400


def test_entity_from_another_laundry_is_a_404(registries):
    hotels, _ = registries
    _seed(hotels, "hotel_id", "rival-hotel", laundry=OTHER)

    with pytest.raises(HTTPException) as err:
        validate_entity_association(
            tag="hotel", hotel_id="rival-hotel", shop_id=None, laundry_id=LAUNDRY
        )
    assert err.value.status_code == 404


def test_suspended_entity_is_a_403(registries):
    hotels, _ = registries
    _seed(hotels, "hotel_id", "closed", status="SUSPENDED")

    with pytest.raises(HTTPException) as err:
        validate_entity_association(
            tag="hotel", hotel_id="closed", shop_id=None, laundry_id=LAUNDRY
        )
    assert err.value.status_code == 403


def test_malformed_entity_id_is_a_400(registries):
    with pytest.raises(HTTPException) as err:
        validate_entity_association(
            tag="hotel", hotel_id="Not A Slug!", shop_id=None, laundry_id=LAUNDRY
        )
    assert err.value.status_code == 400


def test_valid_entity_returns_only_its_own_field(registries):
    hotels, _ = registries
    _seed(hotels, "hotel_id", "cinnamon-grand")

    assert validate_entity_association(
        tag="hotel", hotel_id="cinnamon-grand", shop_id=None, laundry_id=LAUNDRY
    ) == {"hotel_id": "cinnamon-grand"}


# ── end to end through the API ────────────────────────────────────────────────


def test_create_stores_the_entity_reference(quots, registries):
    client, raw = quots
    hotels, _ = registries
    _seed(hotels, "hotel_id", "cinnamon-grand", name="Cinnamon Grand")

    resp = client.post(
        "/quotations",
        json={
            "client_name": "Cinnamon Grand",
            "tag": "hotel",
            "hotel_id": "cinnamon-grand",
            "line_items": [],
        },
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["hotel_id"] == "cinnamon-grand"

    stored = raw.find_one({"laundry_id": LAUNDRY})
    assert stored["hotel_id"] == "cinnamon-grand"
    assert "shop_id" not in stored


def test_create_rejects_a_hotel_id_on_a_shop_quotation(quots, registries):
    client, raw = quots
    hotels, _ = registries
    _seed(hotels, "hotel_id", "cinnamon-grand")

    resp = client.post(
        "/quotations",
        json={
            "client_name": "Cinnamon Grand",
            "tag": "shop",
            "hotel_id": "cinnamon-grand",
            "line_items": [],
        },
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert resp.status_code == 400
    assert raw.count_documents({}) == 0


def test_create_without_entity_still_works(quots, registries):
    """Walk-in customers keep the name-only path."""
    client, raw = quots

    resp = client.post(
        "/quotations",
        json={"client_name": "Walk In", "tag": "shop", "line_items": []},
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["hotel_id"] is None
    assert resp.json()["shop_id"] is None


def test_flipping_the_tag_clears_the_old_entity(quots, registries):
    """A shop quotation switched to a hotel tag must not keep a shop_id."""
    client, raw = quots
    hotels, shops = registries
    _seed(shops, "shop_id", "city-express")
    _seed(hotels, "hotel_id", "cinnamon-grand")

    created = client.post(
        "/quotations",
        json={
            "client_name": "City Express",
            "tag": "shop",
            "shop_id": "city-express",
            "line_items": [],
        },
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert created.status_code == 201, created.text
    qid = created.json()["id"]

    updated = client.put(
        f"/quotations/{qid}",
        json={"tag": "hotel", "client_name": "Cinnamon Grand"},
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert updated.status_code == 200, updated.text
    body = updated.json()
    # The shop association belonged to the old side; flipping the tag must drop
    # it rather than reject the correction or leave a contradictory id behind.
    assert body["shop_id"] is None
    assert body["hotel_id"] is None
