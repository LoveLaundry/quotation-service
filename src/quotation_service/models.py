from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, Float, JSON

from .database import Base

class Quotation(Base):
    __tablename__ = "quotations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    item_name: Mapped[str] = mapped_column(String, nullable=False)
    size: Mapped[str] = mapped_column(String, nullable=False)
    unit_price_with_options: Mapped[dict] = mapped_column(JSON, nullable=False)