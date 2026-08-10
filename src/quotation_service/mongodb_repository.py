from datetime import datetime
from typing import List, Optional, Dict, Any
from pymongo import MongoClient
from bson import ObjectId

from .repository import QuotationRepository
from .config import DATABASE_URL, MONGODB_DB_NAME, MONGODB_COLLECTION
from .crypto_helper import encrypt_dict, decrypt_dict, get_search_token

SENSITIVE_FIELDS = ["client_name", "quotation_title", "line_items"]


class MongoDBQuotationRepository(QuotationRepository):
    """MongoDB implementation of QuotationRepository with envelope encryption"""

    def __init__(self):
        self.client = MongoClient(DATABASE_URL)
        self.db = self.client[MONGODB_DB_NAME]
        self.collection = self.db[MONGODB_COLLECTION]

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
                results.append(self._serialize_document(doc))
            except ValueError:
                pass
        return results

    def get_by_id(self, quotation_id: str) -> Optional[Dict[str, Any]]:
        """Get a quotation by ID"""
        try:
            doc = self.collection.find_one({"_id": ObjectId(quotation_id)})
            return self._serialize_document(doc) if doc else None
        except Exception:
            return None

    def create(self, quotation_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new quotation"""
        now = datetime.utcnow()

        document = {
            "client_name": quotation_data["client_name"],
            "quotation_title": quotation_data.get("quotation_title"),
            "line_items": quotation_data.get("line_items", []),
            "status": quotation_data.get("status", "draft"),
            "created_at": now,
            "updated_at": now,
        }

        # Encrypt representation of document
        encrypted_document = encrypt_dict(document, SENSITIVE_FIELDS)

        result = self.collection.insert_one(encrypted_document)
        encrypted_document["_id"] = result.inserted_id

        return self._serialize_document(encrypted_document)

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

            return self._serialize_document(result) if result else None
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
        if self.client:
            self.client.close()
