"""Seed default storage locations and tags."""
from sqlalchemy.orm import Session
from app.models import StorageLocation, Tag


DEFAULT_LOCATIONS = ["Pantry", "Fridge", "Freezer"]

DEFAULT_TAGS = [
    {"name": "Prepared Meals", "is_default": True},
    {"name": "Snacks", "is_default": True},
]


def seed_locations(db: Session) -> None:
    existing = {loc.name for loc in db.query(StorageLocation).all()}
    for name in DEFAULT_LOCATIONS:
        if name not in existing:
            db.add(StorageLocation(name=name))
    db.commit()


def seed_tags(db: Session) -> None:
    existing = {t.name for t in db.query(Tag).all()}
    for tag_def in DEFAULT_TAGS:
        if tag_def["name"] not in existing:
            db.add(Tag(name=tag_def["name"], is_default=tag_def["is_default"]))
    db.commit()
