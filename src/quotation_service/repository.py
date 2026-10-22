from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any


class QuotationRepository(ABC):
    """Abstract repository interface for quotation operations.

    Every tenant-shaped method accepts an optional ``laundry_id``. ``None``
    means "unscoped" and is reserved for guest/platform/system callers that
    run outside a request (sync workers, migrations). Request-scoped callers
    MUST pass the tenant resolved from the signed JWT.
    """

    @abstractmethod
    def get_all(self, laundry_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all quotations for a laundry (or all when laundry_id is None)"""
        pass

    @abstractmethod
    def get_by_id(self, quotation_id: Any, laundry_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get a quotation by ID, scoped to a laundry when provided"""
        pass

    @abstractmethod
    def get_by_tag(self, tag: str, laundry_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get quotations filtered by tag, scoped to a laundry when provided"""
        pass

    @abstractmethod
    def create(self, quotation_data: Dict[str, Any], laundry_id: Optional[str] = None) -> Dict[str, Any]:
        """Create a new quotation, stamping laundry_id when provided"""
        pass

    @abstractmethod
    def update(self, quotation_id: Any, update_data: Dict[str, Any], laundry_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Update an existing quotation, scoped to a laundry when provided"""
        pass

    @abstractmethod
    def delete(self, quotation_id: Any, laundry_id: Optional[str] = None) -> bool:
        """Delete a quotation, scoped to a laundry when provided"""
        pass

    @abstractmethod
    def get_public_by_id(self, quotation_id: Any) -> Optional[Dict[str, Any]]:
        """Capability read for unauthenticated guest tracking (no tenant)."""
        pass

    @abstractmethod
    def get_public_shop_quotations(self) -> List[Dict[str, Any]]:
        """Public storefront listing for the configured public laundry."""
        pass
    
    @abstractmethod
    def create_tags(
        self,
        quotation_id: Any,
        count: int,
        per_item: bool,
        label: Optional[str],
        laundry_id: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Mint garment tags for a quotation, scoped to a laundry when provided"""
        pass

    @abstractmethod
    def list_tags(self, quotation_id: Any, laundry_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List garment tags for a quotation, scoped to a laundry when provided"""
        pass

    @abstractmethod
    def get_tag(self, code: str, laundry_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Resolve a garment tag by code, scoped to a laundry when provided"""
        pass
