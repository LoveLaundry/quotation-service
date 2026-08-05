from pydantic import BaseModel, Field


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


class QuotationUpdate(BaseModel):
    client_name: str | None = None
    quotation_title: str | None = None
    line_items: list[LineItemSchema] | None = None
    status: str | None = None


class QuotationResponse(BaseModel):
    id: int
    client_name: str
    quotation_title: str | None
    line_items: list[dict]
    status: str
    created_at: str | None
    updated_at: str | None

    model_config = {"from_attributes": True}
