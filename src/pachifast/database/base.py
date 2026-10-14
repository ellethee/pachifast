"""Declarative base shared by every pachifast model (SQLAlchemy 2.0 ORM, no extra layer)."""

from sqlalchemy.orm import DeclarativeBase, MappedAsDataclass


class Base(MappedAsDataclass, DeclarativeBase, kw_only=True, eq=False):
    """Base class of the ORM models of the core and of the application modules.

    Models are declared with ``Mapped[...]`` annotations and ``mapped_column()``. They are also
    keyword-only dataclasses, so the Python defaults (``default=``, ``default_factory=``) apply when an
    instance is created, before any flush::

        class Invoice(Base):
            __tablename__ = "invoice"

            id: Mapped[int | None] = mapped_column(primary_key=True, default=None, nullable=False)
            number: Mapped[str] = mapped_column(String(30), unique=True)
            paid: Mapped[bool] = mapped_column(default=False)

    The API schemas are separate Pydantic models (see ``AliasedBaseModel``); read an ORM row with
    ``Schema.model_validate(row)``. All the models share ``Base.metadata``, which is what the
    migrations compare against the database.
    """
