from __future__ import annotations

import secrets
from datetime import date, datetime, timezone
from typing import List, Optional

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Table, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

item_tags = Table(
    "item_tags",
    Base.metadata,
    Column("item_id", Integer, ForeignKey("food_items.id"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id"), primary_key=True),
    UniqueConstraint("item_id", "tag_id"),
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _generate_public_id() -> str:
    return secrets.token_urlsafe(9)  # 12 chars, URL-safe


class FoodItem(Base):
    __tablename__ = "food_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, default=_generate_public_id
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    revisions: Mapped[List[ItemRevision]] = relationship(
        back_populates="item", order_by="ItemRevision.revision_num"
    )
    tags: Mapped[List[Tag]] = relationship(secondary=item_tags, back_populates="items")

    @property
    def latest_revision(self) -> Optional[ItemRevision]:
        return self.revisions[-1] if self.revisions else None

    @property
    def latest_active_revision(self) -> Optional[ItemRevision]:
        for rev in reversed(self.revisions):
            if not rev.is_deleted:
                return rev
        return None

    @property
    def is_deleted(self) -> bool:
        latest = self.latest_revision
        return latest.is_deleted if latest else False


class ItemRevision(Base):
    __tablename__ = "item_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("food_items.id"), index=True)
    revision_num: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    date_prepared: Mapped[date] = mapped_column(Date)
    expiration_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    storage_location_id: Mapped[int] = mapped_column(
        ForeignKey("storage_locations.id")
    )
    photo_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    amount_unit: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    item: Mapped[FoodItem] = relationship(back_populates="revisions")
    links: Mapped[List[RevisionLink]] = relationship(
        back_populates="revision", cascade="all, delete-orphan"
    )
    storage_location: Mapped[StorageLocation] = relationship()


class RevisionLink(Base):
    __tablename__ = "revision_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision_id: Mapped[int] = mapped_column(
        ForeignKey("item_revisions.id"), index=True
    )
    url: Mapped[str] = mapped_column(Text)
    label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    revision: Mapped[ItemRevision] = relationship(back_populates="links")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    items: Mapped[List[FoodItem]] = relationship(secondary=item_tags, back_populates="tags")


class StorageLocation(Base):
    __tablename__ = "storage_locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
