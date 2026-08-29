from datetime import datetime
from typing import Union, Literal, Optional
from pydantic import BaseModel, Field, field_serializer


# ─── Order lifecycle ──────────────────────────────────────────────────────────
# Laundry-industry standard processing pipeline (received → washed → pressed →
# folded → packed → ready → out for delivery → delivered). A quotation/order
# moves through these stages; history is recorded on every transition.
ORDER_STATUSES = [
    "draft",
    "received",
    "washing",
    "pressing",
    "folding",
    "packing",
    "ready",
    "out_for_delivery",
    "delivered",
    "cancelled",
]

ORDER_STATUS_TRANSITIONS: dict[str, list[str]] = {
    "draft": ["received", "cancelled"],
    "received": ["washing", "cancelled"],
    "washing": ["pressing", "cancelled"],
    "pressing": ["folding", "cancelled"],
    "folding": ["packing", "cancelled"],
    "packing": ["ready", "cancelled"],
    "ready": ["out_for_delivery", "cancelled"],
    "out_for_delivery": ["delivered", "cancelled"],
    "delivered": [],
    "cancelled": [],
}


class StatusHistoryEntry(BaseModel):
    status: str
    changed_at: str
    changed_by: Optional[str] = None
    note: Optional[str] = None


class LineItemSchema(BaseModel):
    item_name: str
    category: str | None = None
    unit_price: float
    notes: str | None = None


class QuotationCreate(BaseModel):
    client_name: str
    quotation_title: str | None = None
    line_items: list[LineItemSchema] = Field(default_factory=list)
    status: str = "draft"
    tag: Literal["shop", "hotel"] = "shop"  # Tag for filtering quotations


class QuotationUpdate(BaseModel):
    client_name: str | None = None
    quotation_title: str | None = None
    line_items: list[LineItemSchema] | None = None
    status: str | None = None
    tag: Literal["shop", "hotel"] | None = None


class QuotationAdvanceStatus(BaseModel):
    """Advance an order to the next lifecycle stage."""

    status: str
    note: Optional[str] = None


class QuotationResponse(BaseModel):
    id: Union[int, str]  # MongoDB uses string IDs, PostgreSQL uses integer IDs
    client_name: str
    quotation_title: str | None
    line_items: list[dict]
    status: str
    status_history: list[StatusHistoryEntry] = []
    tag: str = "shop"
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_serializer('created_at', 'updated_at')
    def serialize_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.isoformat()

    model_config = {"from_attributes": True}
