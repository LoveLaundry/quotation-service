from datetime import datetime
from typing import List, Optional, Dict, Any
from bson import ObjectId

from .repository import QuotationRepository
from .config import MONGODB_COLLECTION
from .crypto_helper import encrypt_dict, decrypt_dict, get_search_token
from .database.main_db import quotations_collection
from .repositories.main_repository import bump_version, enqueue_sync
from .services.verification_service import attach_verification_to

SENSITIVE_FIELDS = ["client_name", "quotation_title", "line_items"]


class MongoDBQuotationRepository(QuotationRepository):
    """MongoDB implementation of QuotationRepository with envelope encryption"""

    def __init__(self):
        self.collection = quotations_collection

        # Create indexes
        self.collection.create_index("client_name_search")
        self.collection.create_index("created_at")

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

    def get_all(self) -> List[Dict[str, Any]]:
        """Get all quotations, sorted by created_at descending"""
        documents = self.collection.find().sort("created_at", -1)
        results = []
        for doc in documents:
            try:
                serialized = self._serialize_document(doc)
                results.append(attach_verification_to("quotation", doc["_id"], serialized))
            except ValueError:
                pass
        return results

    def get_by_id(self, quotation_id: str) -> Optional[Dict[str, Any]]:
        """Get a quotation by ID"""
        try:
            doc = self.collection.find_one({"_id": ObjectId(quotation_id)})
            if not doc:
                return None
            serialized = self._serialize_document(doc)
            return attach_verification_to("quotation", doc["_id"], serialized)
        except Exception:
            return None

    def get_by_tag(self, tag: str) -> List[Dict[str, Any]]:
        """Get quotations filtered by tag"""
        documents = self.collection.find({"tag": tag}).sort("created_at", -1)
        results = []
        for doc in documents:
            try:
                serialized = self._serialize_document(doc)
                results.append(attach_verification_to("quotation", doc["_id"], serialized))
            except ValueError:
                pass
        return results

    def create(self, quotation_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new quotation"""
        now = datetime.utcnow()

        document = {
            "client_name": quotation_data["client_name"],
            "quotation_title": quotation_data.get("quotation_title"),
            "line_items": quotation_data.get("line_items", []),
            "status": quotation_data.get("status", "draft"),
            "tag": quotation_data.get("tag", "shop"),
            "created_at": now,
            "updated_at": now,
        }

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
        self, quotation_id: str, update_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update an existing quotation"""
        try:
            # We need to fetch original document first to merge and encrypt properly
            original = self.collection.find_one({"_id": ObjectId(quotation_id)})
            if not original:
                return None

            try:
                decrypted_original = self.decrypt_document_full(original)
            except Exception:
                return None

            # Merge updates
            decrypted_original.update(update_data)
            decrypted_original.pop("created_at", None)
            decrypted_original["updated_at"] = datetime.utcnow()

            # Encrypt new state
            encrypted_new = encrypt_dict(decrypted_original, SENSITIVE_FIELDS)

            result = self.collection.find_one_and_update(
                {"_id": ObjectId(quotation_id)},
                {"$set": encrypted_new},
                return_document=True,
            )

            if not result:
                return None

            # Main DB write succeeded -> bump version and enqueue replication
            new_version = bump_version("quotation", ObjectId(quotation_id))
            enqueue_sync("quotation", ObjectId(quotation_id), new_version)

            serialized = self._serialize_document(result)
            return attach_verification_to("quotation", ObjectId(quotation_id), serialized)
        except Exception:
            return None

    def delete(self, quotation_id: str) -> bool:
        """Delete a quotation"""
        try:
            result = self.collection.delete_one({"_id": ObjectId(quotation_id)})
            return result.deleted_count > 0
        except Exception:
            return False

    def close(self):
        """Close the MongoDB connection"""
        pass