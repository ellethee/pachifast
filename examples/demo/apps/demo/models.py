"""Tables of the demo app: the sample records of the table/dialog tests and the uploaded files."""

import uuid
from datetime import datetime

from sqlalchemy import Float, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pachifast.database import Base
from pachifast.database.utils import UtcDateTime, utcnow


class DemoItem(Base):
    """Sample record: every column has a different kind of value, to try the table filters and the edit forms."""

    __tablename__ = "demo_item"

    id: Mapped[int | None] = mapped_column(primary_key=True, default=None, nullable=False)
    name: Mapped[str] = mapped_column(String(120), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True, default="draft")
    price: Mapped[float] = mapped_column(Float, default=0.0)
    quantity: Mapped[int] = mapped_column(default=0)
    active: Mapped[bool] = mapped_column(default=True)
    country_id: Mapped[int | None] = mapped_column(default=None)
    city_id: Mapped[int | None] = mapped_column(default=None)
    notes: Mapped[str | None] = mapped_column(String(500), default=None)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default_factory=utcnow)


class DemoFile(Base):
    """An uploaded file; the content lives on disk under the name ``<id>`` (never the client's name)."""

    __tablename__ = "demo_file"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default_factory=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int]
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default_factory=utcnow)
