from fastapi.testclient import TestClient

from quotation_service.main import app
from quotation_service.database import Base, engine, SessionLocal
from quotation_service.models import Quotation


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_duplicate_category_item_is_rejected():
    client = TestClient(app)

    response = client.post(
        "/quotations",
        json={
            "item_name": "Tea",
            "category": "Hotel A",
            "size": "Large",
            "unit_price_with_options": {"price": 10},
        },
    )
    assert response.status_code == 201

    duplicate = client.post(
        "/quotations",
        json={
            "item_name": "Tea",
            "category": "Hotel A",
            "size": "Small",
            "unit_price_with_options": {"price": 12},
        },
    )

    assert duplicate.status_code == 400
    assert "already exists" in duplicate.json()["detail"].lower()
