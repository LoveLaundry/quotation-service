from pydantic import BaseModel


class QuotationCreate(BaseModel):
    item_name: str
    category: str
    size: str
    unit_price_with_options: dict


class QuotationUpdate(BaseModel):
    item_name: str
    category: str
    size: str
    unit_price_with_options: dict


class QuotationResponse(BaseModel):
    id: int
    item_name: str
    category: str
    size: str
    unit_price_with_options: dict

    model_config = {
        "from_attributes": True
    }