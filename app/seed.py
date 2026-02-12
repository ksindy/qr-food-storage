"""Seed default storage locations."""
from sqlalchemy.orm import Session
from app.models import StorageLocation


DEFAULT_LOCATIONS = ["Pantry", "Fridge", "Freezer"]


def seed_locations(db: Session) -> None:
    existing = {loc.name for loc in db.query(StorageLocation).all()}
    for name in DEFAULT_LOCATIONS:
        if name not in existing:
            db.add(StorageLocation(name=name))
    db.commit()
