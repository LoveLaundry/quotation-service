"""Tenant isolation for the bills domain.

Contract (see architecture_updated_system/ARCHITECTURE.md):
- The trusted tenant comes ONLY from the signed JWT claims resolved here —
  never from request bodies, query params, URLs, storage, or headers.
- Every tenant query is scoped with ``{"laundry_id": tid}``. There is no
  unscoped fallback: a missing context fails closed.
- Referenced records (bill -> gate pass -> delivery) must belong to the same
  laundry; cross-tenant linkage is rejected before any mutation.
- Suspended/archived snapshots in the token fail closed. Fresh membership
  state is enforced at login/switch time in user-service; resource services
  additionally honor the snapshot (see residual R-note in THREAT_MODEL.md).
"""
import re
from typing import Any, Dict, Optional

from fastapi import HTTPException

LOVE_LAUNDRY_ID = "lovelaundry"

ACTIVE = "ACTIVE"


def _claims_entries(current_user: dict) -> list:
    entries = current_user.get("laundries") or []
    return entries if isinstance(entries, list) else []


def is_platform_context(current_user: dict) -> bool:
    """Audited platform support entry (short-lived, laundry-scoped)."""
    return bool(current_user.get("platform_admin")) or bool(current_user.get("support_entry"))


def resolve_laundry_id(current_user: Optional[dict]) -> str:
    """Return the operable laundry for this session or raise.

    Rules:
    - No session / no laundry claim -> 400 (switch to a laundry first).
    - Platform support-entry token -> its (audited) laundry claim.
    - Otherwise the laundry must appear in the token's membership snapshot
      with ACTIVE status; anything else (unknown, suspended, archived) -> 403.
    """
    if not isinstance(current_user, dict):
        raise HTTPException(status_code=401, detail="Not authenticated")
    laundry_id = (current_user.get("laundry_id") or "").strip().lower()
    if not laundry_id:
        raise HTTPException(
            status_code=400,
            detail="No active laundry in this session — switch laundry first",
        )
    if is_platform_context(current_user):
        return laundry_id
    for entry in _claims_entries(current_user):
        if not isinstance(entry, dict):
            continue
        if (entry.get("id") or "").strip().lower() == laundry_id:
            if (entry.get("status") or "").strip().upper() == ACTIVE:
                return laundry_id
            raise HTTPException(
                status_code=403,
                detail="This laundry is not active — contact an administrator",
            )
    raise HTTPException(
        status_code=403,
        detail="No active assignment in that laundry",
    )


def laundry_match(laundry_id: str) -> dict:
    """Mongo filter matching exactly one laundry's documents.

    Transitional rule: documents predating multi-tenancy carry no
    ``laundry_id`` and can only belong to LoveLaundry, the sole historical
    tenant — so for ``lovelaundry`` they match too. Any other laundry matches
    strictly its own stamped documents. New writes always stamp, and the
    migration stamps the rest, after which the fallback matches nothing.
    """
    if laundry_id == LOVE_LAUNDRY_ID:
        return {
            "$or": [
                {"laundry_id": laundry_id},
                {"laundry_id": {"$exists": False}},
                {"laundry_id": None},
            ]
        }
    return {"laundry_id": laundry_id}


def scoped_query(query: Optional[Dict[str, Any]], laundry_id: str) -> Dict[str, Any]:
    """Return a copy of a Mongo filter scoped to exactly one laundry."""
    scoped = dict(query or {})
    match = laundry_match(laundry_id)
    if "$or" in match and scoped:
        return {"$and": [scoped, match]}
    if "$or" in match:
        return match
    scoped.update(match)
    return scoped


def assert_same_laundry(doc: Optional[Dict[str, Any]], laundry_id: str, label: str = "record"):
    """Fail closed unless the stored document belongs to this laundry.

    Same transitional rule as :func:`laundry_match`: an unstamped document
    reads as LoveLaundry's. Anything else foreign (or missing) is a 404 so
    identifiers cannot be probed across laundries.
    """
    actual = None
    if isinstance(doc, dict):
        actual = doc.get("laundry_id") or LOVE_LAUNDRY_ID
    if actual != laundry_id:
        raise HTTPException(status_code=404, detail=f"{label} not found")


ACTIVE = "ACTIVE"

# Which entity type each quotation tag may be associated with. A "shop"
# quotation may name a shop and must never name a hotel, and vice versa.
TAG_ENTITIES = {"shop": "SHOP", "hotel": "HOTEL"}


def normalize_entity_id(value: Any) -> str:
    """Validate an entity id shape before it is used in a query."""
    slug = str(value or "").strip().lower()
    if not slug:
        return ""
    if not re.match(r"^[a-z0-9][a-z0-9_-]{0,31}$", slug):
        raise HTTPException(
            status_code=400,
            detail="Invalid entity id — use 1-32 lowercase letters, digits, '-' or '_'",
        )
    return slug


def validate_entity_association(
    *,
    tag: Any,
    hotel_id: Any,
    shop_id: Any,
    laundry_id: str,
) -> Dict[str, Any]:
    """Return the single entity reference a quotation may carry.

    Enforces, in order:
    - an id supplied for the side that does not match ``tag`` is rejected, so a
      hotel quotation can never be filed against a shop;
    - the id must exist in the ``hotels``/``shops`` registry **in this laundry**
      — an id owned by another tenant reports 404, which keeps ids from being
      probed across tenants;
    - the entity must be ACTIVE: suspended customers do not accrue new work.

    Returns ``{}`` when no entity was supplied, which is the walk-in case and
    leaves the quotation keyed by ``client_name`` exactly as before.
    """
    side = TAG_ENTITIES.get(str(tag or "shop").strip().lower(), "SHOP")
    wanted_raw = hotel_id if side == "HOTEL" else shop_id
    foreign_raw = shop_id if side == "HOTEL" else hotel_id

    if str(foreign_raw or "").strip():
        raise HTTPException(
            status_code=400,
            detail=f"A {side.lower()}-tagged quotation cannot reference a "
            f"{'shop' if side == 'HOTEL' else 'hotel'} entity",
        )

    wanted = normalize_entity_id(wanted_raw)
    if not wanted:
        return {}

    from .database.main_db import hotels_collection, shops_collection

    collection = hotels_collection if side == "HOTEL" else shops_collection
    field = "hotel_id" if side == "HOTEL" else "shop_id"
    doc = collection.find_one({field: wanted, "associated_laundry_id": laundry_id})
    if not doc:
        raise HTTPException(status_code=404, detail="No such entity in this laundry")
    if str(doc.get("status") or "").strip().upper() != ACTIVE:
        raise HTTPException(status_code=403, detail="That entity is not active")

    return {field: wanted}


def owned_filter(oid, laundry_id: str) -> dict:
    """Write filter binding one document id to exactly one laundry."""
    filt: Dict[str, Any] = {"_id": oid}
    filt.update(laundry_match(laundry_id))
    return filt


def stamp_missing_laundry_ids(collections: Dict[str, Any], laundry_id: str = LOVE_LAUNDRY_ID) -> Dict[str, int]:
    """Backfill ``laundry_id`` on documents that predate multi-tenancy.

    Only documents WITHOUT the field are touched; existing values (including
    other laundries) are never rewritten. Returns per-collection counts of
    stamped documents for the reconciliation check. The operator runs this
    per collection before switching traffic, then verifies totals.

    Synchronous on purpose: this service talks to MongoDB through plain
    ``pymongo`` (not motor), so ``update_many`` returns a result directly.
    """
    stamped: Dict[str, int] = {}
    for name, collection in collections.items():
        result = collection.update_many(
            {"$or": [{"laundry_id": {"$exists": False}}, {"laundry_id": None}]},
            {"$set": {"laundry_id": laundry_id}},
        )
        stamped[name] = result.modified_count
    return stamped


def match_stage(laundry_id: str) -> dict:
    """Aggregation $match stage restricting to one laundry's documents."""
    return {"$match": laundry_match(laundry_id)}
