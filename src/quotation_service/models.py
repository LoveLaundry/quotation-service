from sqlalchemy import String, Integer, JSON, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Quotation(Base):
    __tablename__ = "quotations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    client_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    quotation_title: Mapped[str | None] = mapped_column(String, nullable=True)
    line_items: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    status_history: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    tag: Mapped[str] = mapped_column(String, nullable=False, default="shop", index=True)
    created_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )
    updated_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=True,
    )


class GarmentTag(Base):
    """A scannable QR tag bound to an order (and optionally a single line item)."""

    __tablename__ = "garment_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    quotation_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    line_item_id: Mapped[str | None] = mapped_column(String, nullable=True)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )
