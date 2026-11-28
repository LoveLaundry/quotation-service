"""Resolve quotation display fields from the authoritative entity registry.

Quotations store only the entity id. Names and codes are read from the same
``hotels``/``shops`` collections that back the hotels list API, so a rename in
the registry is reflected everywhere a quotation is shown without rewriting
historical price lists.
"""
from typing import Any, Optional

from .tenancy import TAG_ENTITIES, normalize_entity_id

QUOTATION_FIELDS = (
    "id",
    "client_name",
    "quotation_title",
    "line_items",
    "status",
    "status_history",
    "tag",
    "hotel_id",
    "shop_id",
    "laundry_id",
    "created_at",
    "updated_at",
)


def _registry_document(
    *,
    tag: Any,
    hotel_id: Any,
    shop_id: Any,
    laundry_id: str,
) -> Optional[dict[str, Any]]:
    side = TAG_ENTITIES.get(str(tag or "shop").strip().lower(), "SHOP")
    wanted = normalize_entity_id(hotel_id if side == "HOTEL" else shop_id)
    if not wanted:
        return None

    from .database.main_db import hotels_collection, shops_collection

    collection = hotels_collection if side == "HOTEL" else shops_collection
    field = "hotel_id" if side == "HOTEL" else "shop_id"
    doc = collection.find_one(
        {field: wanted, "associated_laundry_id": laundry_id},
    )
    return doc if isinstance(doc, dict) else None


def registry_display(
    *,
    tag: Any,
    hotel_id: Any,
    shop_id: Any,
    laundry_id: str,
) -> tuple[Optional[str], Optional[str]]:
    """Return the registry name and code for a quotation's entity."""
    doc = _registry_document(
        tag=tag, hotel_id=hotel_id, shop_id=shop_id, laundry_id=laundry_id
    )
    if not doc:
        return None, None
    side = TAG_ENTITIES.get(str(tag or "shop").strip().lower(), "SHOP")
    code_field = "hotel_code" if side == "HOTEL" else "shop_code"
    name = str(doc.get("name") or "").strip() or None
    code = str(doc.get(code_field) or "").strip() or None
    return name, code


def hydrate_quotation(quotation: Any, laundry_id: str) -> dict[str, Any]:
    """Copy a repository row and replace its display name with registry data."""
    if isinstance(quotation, dict):
        data = dict(quotation)
    else:
        data = {
            field: getattr(quotation, field)
            for field in QUOTATION_FIELDS
            if hasattr(quotation, field)
        }

    tag = data.get("tag")
    hotel_id = data.get("hotel_id")
    shop_id = data.get("shop_id")
    name, code = registry_display(
        tag=tag, hotel_id=hotel_id, shop_id=shop_id, laundry_id=laundry_id
    )
    if name:
        data["client_name"] = name
    if code:
        data["hotel_code" if str(tag).lower() == "hotel" else "shop_code"] = code
    return data


def hydrate_quotations(quotations: Any, laundry_id: str) -> list[dict[str, Any]]:
    return [hydrate_quotation(item, laundry_id) for item in quotations or []]
