from typing import List, Optional, Dict, Any
import secrets
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .repository import QuotationRepository
from .models import Quotation, GarmentTag
from .tenancy import LOVE_LAUNDRY_ID


class PostgreSQLQuotationRepository(QuotationRepository):
    """PostgreSQL/SQLite implementation of QuotationRepository using SQLAlchemy"""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _tenant_clause(column, laundry_id: Optional[str]):
        """Build the tenant predicate, mirroring the Mongo legacy rule.

        ``lovelaundry`` (the sole pre-tenancy tenant) also matches rows whose
        nullable ``laundry_id`` is NULL, so historical rows stay readable.
        """
        if laundry_id == LOVE_LAUNDRY_ID:
            return or_(column == laundry_id, column.is_(None))
        return column == laundry_id

    def _model_to_dict(self, quotation: Quotation) -> Dict[str, Any]:
        """Convert SQLAlchemy model to dictionary"""
        return {
            "id": quotation.id,
            "client_name": quotation.client_name,
            "quotation_title": quotation.quotation_title,
            "line_items": quotation.line_items,
            "status": quotation.status,
            "status_history": quotation.status_history or [],
            "tag": quotation.tag,
            "laundry_id": quotation.laundry_id,
            "created_at": quotation.created_at,
            "updated_at": quotation.updated_at,
        }

    def get_all(self, laundry_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all quotations for a laundry, sorted by created_at descending"""
        query = self.db.query(Quotation)
        if laundry_id is not None:
            query = query.filter(self._tenant_clause(Quotation.laundry_id, laundry_id))
        quotations = query.order_by(Quotation.created_at.desc()).all()
        return [self._model_to_dict(q) for q in quotations]

    def get_by_id(self, quotation_id: int, laundry_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get a quotation by ID, scoped to a laundry when provided"""
        query = self.db.query(Quotation).filter(Quotation.id == quotation_id)
        if laundry_id is not None:
            query = query.filter(self._tenant_clause(Quotation.laundry_id, laundry_id))
        quotation = query.first()
        return self._model_to_dict(quotation) if quotation else None

    def get_by_tag(self, tag: str, laundry_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get quotations filtered by tag, scoped to a laundry when provided"""
        query = self.db.query(Quotation).filter(Quotation.tag == tag)
        if laundry_id is not None:
            query = query.filter(self._tenant_clause(Quotation.laundry_id, laundry_id))
        quotations = query.order_by(Quotation.created_at.desc()).all()
        return [self._model_to_dict(q) for q in quotations]

    def get_public_by_id(self, quotation_id: int) -> Optional[Dict[str, Any]]:
        """Capability read for unauthenticated guest tracking (no tenant)."""
        return self.get_by_id(quotation_id)

    def get_public_shop_quotations(self) -> List[Dict[str, Any]]:
        """Public storefront listing for the configured public laundry."""
        return self.get_by_tag("shop")
    
    def create(self, quotation_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new quotation"""
        new_quotation = Quotation(
            client_name=quotation_data["client_name"],
            quotation_title=quotation_data.get("quotation_title"),
            line_items=quotation_data.get("line_items", []),
            status=quotation_data.get("status", "draft"),
            status_history=quotation_data.get("status_history", []),
            tag=quotation_data.get("tag", "shop"),
            laundry_id=laundry_id,
        )

        self.db.add(new_quotation)
        self.db.commit()
        self.db.refresh(new_quotation)

        return self._model_to_dict(new_quotation)

    def update(self, quotation_id: int, update_data: Dict[str, Any], laundry_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Update an existing quotation, scoped to a laundry when provided"""
        query = self.db.query(Quotation).filter(Quotation.id == quotation_id)
        if laundry_id is not None:
            query = query.filter(self._tenant_clause(Quotation.laundry_id, laundry_id))
        quotation = query.first()
        if not quotation:
            return None

        for key, value in update_data.items():
            if hasattr(quotation, key):
                setattr(quotation, key, value)

        self.db.commit()
        self.db.refresh(quotation)

        return self._model_to_dict(quotation)

    def delete(self, quotation_id: int, laundry_id: Optional[str] = None) -> bool:
        """Delete a quotation, scoped to a laundry when provided"""
        query = self.db.query(Quotation).filter(Quotation.id == quotation_id)
        if laundry_id is not None:
            query = query.filter(self._tenant_clause(Quotation.laundry_id, laundry_id))
        quotation = query.first()
        if not quotation:
            return False

        self.db.delete(quotation)
        self.db.commit()

        return True

    # ─── Garment tags ──────────────────────────────────────────────────────────
    def _tag_to_dict(self, tag: GarmentTag) -> Dict[str, Any]:
        return {
            "id": tag.id,
            "code": tag.code,
            "quotation_id": tag.quotation_id,
            "line_item_id": tag.line_item_id,
            "label": tag.label,
            "laundry_id": tag.laundry_id,
            "created_at": tag.created_at,
        }

    def _gen_tag_code(self) -> str:
        while True:
            code = secrets.token_urlsafe(6)
            if self.db.query(GarmentTag).filter(GarmentTag.code == code).first() is None:
                return code

    def create_tags(
        self,
        quotation_id: int,
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
            tag = GarmentTag(
                code=code,
                quotation_id=int(quotation_id),
                line_item_id=lid,
                label=lab,
                laundry_id=laundry_id,
            )
            self.db.add(tag)
            self.db.commit()
            self.db.refresh(tag)
            created.append(self._tag_to_dict(tag))
        return created

    def list_tags(self, quotation_id: int, laundry_id: Optional[str] = None) -> List[Dict[str, Any]]:
        query = self.db.query(GarmentTag).filter(GarmentTag.quotation_id == int(quotation_id))
        if laundry_id is not None:
            query = query.filter(self._tenant_clause(GarmentTag.laundry_id, laundry_id))
        tags = query.order_by(GarmentTag.created_at.asc()).all()
        return [self._tag_to_dict(t) for t in tags]

    def get_tag(self, code: str, laundry_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        query = self.db.query(GarmentTag).filter(GarmentTag.code == code)
        if laundry_id is not None:
            query = query.filter(self._tenant_clause(GarmentTag.laundry_id, laundry_id))
        tag = query.first()
        return self._tag_to_dict(tag) if tag else None
