from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import StorageLocation
from app.templating import templates

router = APIRouter()


@router.get("/locations", response_class=HTMLResponse)
def locations_list(request: Request, db: Session = Depends(get_db)):
    locations = db.query(StorageLocation).order_by(StorageLocation.name).all()
    return templates.TemplateResponse(
        "locations/list.html",
        {"request": request, "locations": locations},
    )


@router.post("/locations")
def create_location(
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db),
):
    name = name.strip()
    if not name:
        locations = db.query(StorageLocation).order_by(StorageLocation.name).all()
        return templates.TemplateResponse(
            "locations/list.html",
            {"request": request, "locations": locations, "error": "Name is required."},
        )

    existing = db.query(StorageLocation).filter(StorageLocation.name == name).first()
    if existing:
        locations = db.query(StorageLocation).order_by(StorageLocation.name).all()
        return templates.TemplateResponse(
            "locations/list.html",
            {"request": request, "locations": locations, "error": f"'{name}' already exists."},
        )

    db.add(StorageLocation(name=name))
    db.commit()
    return RedirectResponse("/locations", status_code=303)
