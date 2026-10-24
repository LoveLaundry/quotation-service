from .app_time import UTC
from datetime import datetime
from typing import List, Optional, Dict, Any
import logging
import secrets
from bson import ObjectId

from .repository import QuotationRepository
from .config import MONGODB_COLLECTION, PUBLIC_LAUNDRY_ID
from .crypto_helper import encrypt_dict, decrypt_dict, get_search_token
from .database.main_db import quotations_collection
from .repositories.main_repository import bump_version, enqueue_delete, enqueue_sync
from .services.verification_service import attach_verification_to, attach_verifications_bulk
from .tenancy import laundry_match
from .tenant_scope import current_laundry_id

logger = logging.getLogger(__name__)

SENSITIVE_FIELDS = ["client_name", "quotation_title", "line_items"]

# Fields that must never be part of an update payload:
#   _id          — MongoDB rejects writes to the immutable id field.
#   laundry_id   — owned by the tenant guard, never by the request payload.
#   sync_version — owned by the atomic $inc in bump_version(); writing it back
#                  would clobber a concurrent bump.
PROTECTED_FIELDS = ("_id", "laundry_id", "sync_version")


class MongoDBQuotationRepository(QuotationRepository):
    """MongoDB implementation of QuotationRepository with envelope encryption"""

    def __init__(self):
        self.collection = quotations_collection

        # Create indexes
        self.collection.create_index("client_name_search")
        self.collection.create_index("created_at")
        self.collection.create_index("tag")

    def _scope(self, query: Optional[Dict[str, Any]], laundry_id: Optional[str]) -> Dict[str, Any]:
        """Merge a business filter with the tenant scope.

        ``laundry_id is None`` means unscoped (guest/platform/system callers).
        For ``lovelaundry`` the scope also matches pre-tenancy documents with a
        missing/null ``laundry_id`` (see tenancy.laundry_match).
        """
        if laundry_id is None:
            return dict(query or {})
        return scoped_query(query, laundry_id)

    def _serialize_document(self, doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Decrypt and convert MongoDB document to API response format"""
        if not doc:
            return None

        try:
            doc = decrypt_dict(doc, SENSITIVE_FIELDS)
        except Exception as e:
            raise ValueError(f"Failed to decrypt quotation: {str(e)}")

        # Convert ObjectId to string
        if "_id" in doc:
            doc["id"] = str(doc["_id"])
            del doc["_id"]

        return doc

    def decrypt_document_full(self, doc: dict) -> dict:
        meta = doc.get("encryption_metadata")
        if not meta:
            return doc
        return decrypt_dict(doc, SENSITIVE_FIELDS)

    def get_all(self, laundry_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all quotations for a laundry, sorted by created_at descending"""
        documents = self.collection.find(self._scope({}, laundry_id)).sort("created_at", -1)
        results = []
        skipped = 0
        for doc in documents:
            try:
                serialized = self._serialize_document(doc)
                results.append(serialized)
            except ValueError:
                # Never swallow this silently: a document that cannot be
                # decrypted (wrong MASTER_KEY, missing envelope metadata or
                # corrupted ciphertext) would otherwise make the whole listing
                # look empty with no trace of why.
                skipped += 1
                logger.error(
                    "Failed to decrypt quotation %s: %s",
                    doc.get("_id"),
                    exc_info=True,
                )
        if skipped:
            logger.error(
                "%s of %s quotations could not be decrypted and were omitted",
                skipped,
                skipped + len(results),
            )
        return attach_verifications_bulk("quotation", results)

    def get_by_id(self, quotation_id: str, laundry_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get a quotation by ID, scoped to a laundry when provided"""
        try:
            doc = self.collection.find_one(
                self._scope({"_id": ObjectId(quotation_id)}, laundry_id)
            )
            if not doc:
                return None
            serialized = self._serialize_document(doc)
        except ValueError:
            logger.error("Failed to decrypt quotation %s", quotation_id, exc_info=True)
            return None
        return attach_verification_to("quotation", doc["_id"], serialized)

    def get_public_by_id(self, quotation_id: str) -> Optional[Dict[str, Any]]:
        """Unauthenticated capability read for guest tracking.

        The id itself is the credential (nothing enumerable), matching the
        pre-multi-tenant behaviour. No tenant can be derived without a session.
        """
        try:
            doc = self.collection.raw.find_one({"_id": ObjectId(quotation_id)})
        except Exception:
            return None
        if not doc:
            return None
        try:
            return self._serialize_document(doc)
        except ValueError:
            logger.error("Failed to decrypt quotation %s", quotation_id, exc_info=True)
            return None

    def get_public_shop_quotations(self) -> List[Dict[str, Any]]:
        """Guest storefront listing, limited to the public storefront tenant."""
        documents = self.collection.raw.find(
            {"tag": "shop", **laundry_match(PUBLIC_LAUNDRY_ID)}
        ).sort("created_at", -1)
        results = []
        for doc in documents:
            try:
                results.append(self._serialize_document(doc))
            except ValueError:
                logger.error("Failed to decrypt quotation %s", doc.get("_id"), exc_info=True)
        return attach_verifications_bulk("quotation", results)

    def get_by_tag(self, tag: str) -> List[Dict[str, Any]]:
        """Get quotations filtered by tag"""
        documents = self.collection.find({"tag": tag}).sort("created_at", -1)
        results = []
        for doc in documents:
            try:
                serialized = self._serialize_document(doc)
                results.append(serialized)
            except ValueError:
                logger.error("Failed to decrypt quotation %s", doc.get("_id"), exc_info=True)
        return attach_verifications_bulk("quotation", results)

    def create(self, quotation_data: Dict[str, Any], laundry_id: Optional[str] = None) -> Dict[str, Any]:
        """Create a new quotation, stamping laundry_id when provided"""
        now = datetime.now(UTC)

        document = {
            "client_name": quotation_data["client_name"],
            "quotation_title": quotation_data.get("quotation_title"),
            "line_items": quotation_data.get("line_items", []),
            "status": quotation_data.get("status", "draft"),
            "status_history": quotation_data.get("status_history", []),
            "tag": quotation_data.get("tag", "shop"),
            "created_at": now,
            "updated_at": now,
        }
        if laundry_id is not None:
            document["laundry_id"] = laundry_id

        # Encrypt representation of document
        encrypted_document = encrypt_dict(document, SENSITIVE_FIELDS)

        result = self.collection.insert_one(encrypted_document)
        encrypted_document["_id"] = result.inserted_id

        # Main DB write succeeded -> bump version and enqueue replication
        new_version = bump_version("quotation", result.inserted_id)
        enqueue_sync("quotation", result.inserted_id, new_version)

        serialized = self._serialize_document(encrypted_document)
        return attach_verification_to("quotation", result.inserted_id, serialized)

    def update(
        self, quotation_id: str, update_data: Dict[str, Any], laundry_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Update an existing quotation, scoped to a laundry when provided"""
        try:
            scoped = self._scope({"_id": ObjectId(quotation_id)}, laundry_id)
            # We need to fetch original document first to merge and encrypt properly
            original = self.collection.find_one(scoped)
            if not original:
                return None

            try:
                decrypted_original = self.decrypt_document_full(original)
            except Exception:
                logger.error("Failed to decrypt quotation %s", quotation_id, exc_info=True)
                return None

            # Merge updates
            decrypted_original.update(update_data)
            decrypted_original.pop("created_at", None)
            decrypted_original["updated_at"] = datetime.now(UTC)

            # Encrypt new state
            encrypted_new = encrypt_dict(decrypted_original, SENSITIVE_FIELDS)

            # `_id` is immutable in MongoDB and `laundry_id` / `sync_version`
            # are owned by the tenant guard and the version bump. Including
            # them in `$set` makes MongoDB reject the whole update.
            payload = {
                key: val
                for key, val in encrypted_new.items()
                if key not in PROTECTED_FIELDS
            }

            result = self.collection.find_one_and_update(
                scoped,
                {"$set": encrypted_new},
                return_document=True,
            )

            if not result:
                return None

            # Main DB write succeeded -> bump version and enqueue replication
            new_version = bump_version("quotation", oid)
            enqueue_sync("quotation", oid, new_version)

            serialized = self._serialize_document(result)
            return attach_verification_to("quotation", oid, serialized)
        except Exception:
            logger.exception("Failed to update quotation %s", quotation_id)
            return None

    def delete(self, quotation_id: str, laundry_id: Optional[str] = None) -> bool:
        """Delete a quotation, scoped to a laundry when provided"""
        try:
            oid = ObjectId(quotation_id)
            scoped = self._scope({"_id": oid}, laundry_id)
            # Verify ownership before mutating: a foreign/missing document is
            # indistinguishable from a missing one (404 oracle).
            if self.collection.find_one(scoped) is None:
                return False
            # Bump the version while the document still exists, then remove it
            # and enqueue a DELETE so the SECONDARY replica drops the record.
            try:
                new_version = bump_version("quotation", oid)
            except KeyError:
                return False
            result = self.collection.delete_one(scoped)
            if result.deleted_count == 0:
                return False
            enqueue_delete("quotation", oid, new_version)
            return True
        except Exception:
            logger.exception("Failed to delete quotation %s", quotation_id)
            return False

    def close(self):
        """Close the MongoDB connection"""
        pass

    # ─── Garment tags ──────────────────────────────────────────────────────────
    @property
    def _tags(self):
        return self.collection.database["garment_tags"]

    def _gen_tag_code(self) -> str:
        while True:
            code = secrets.token_urlsafe(6)
            if self._tags.find_one({"code": code}) is None:
                return code

    def create_tags(
        self,
        quotation_id: str,
        count: int,
        per_item: bool,
        label: Optional[str],
        laundry_id: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        q = self.get_by_id(quotation_id, laundry_id=laundry_id)
        if not q:
            return None
        items = q.get("line_items") or []
        specs: List[tuple] = []
        if per_item and items:
            for it in items:
                specs.append(
                    (str(it["id"]) if it.get("id") is not None else None, it.get("item_name"))
                )
        else:
            for _ in range(max(1, int(count or 1))):
                specs.append((None, label))
        created: List[Dict[str, Any]] = []
        for lid, lab in specs:
            code = self._gen_tag_code()
            doc = {
                "code": code,
                "quotation_id": str(quotation_id),
                "line_item_id": lid,
                "label": lab,
                "laundry_id": current_laundry_id(),
                "created_at": datetime.now(UTC),
            }
            if laundry_id is not None:
                doc["laundry_id"] = laundry_id
            self._tags.insert_one(doc)
            doc["id"] = str(doc.pop("_id"))
            created.append(doc)
        return created

    def list_tags(self, quotation_id: str, laundry_id: Optional[str] = None) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        query = self._scope({"quotation_id": str(quotation_id)}, laundry_id)
        for d in self._tags.find(query).sort("created_at", 1):
            d["id"] = str(d.pop("_id"))
            out.append(d)
        return out

    def get_tag(self, code: str, laundry_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        d = self._tags.find_one(self._scope({"code": code}, laundry_id))
        if not d:
            return None
        d["id"] = str(d.pop("_id"))
        return d