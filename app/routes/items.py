import re
from datetime import date, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, Form, Query, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import FoodItem, ItemRevision, RevisionLink, StorageLocation, Tag
from app.photo import photo_url, save_photo
from app.qr import generate_qr_png, item_url
from app.templating import templates

router = APIRouter()

URL_RE = re.compile(
    r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE
)


def _get_item_or_404(public_id: str, db: Session) -> FoodItem:
    item = (
        db.query(FoodItem)
        .options(
            joinedload(FoodItem.revisions)
            .joinedload(ItemRevision.links),
            joinedload(FoodItem.revisions)
            .joinedload(ItemRevision.storage_location),
            joinedload(FoodItem.tags),
        )
        .filter(FoodItem.public_id == public_id)
        .first()
    )
    if not item:
        raise _not_found()
    return item


def _not_found():
    from fastapi import HTTPException
    return HTTPException(status_code=404, detail="Item not found")


# --- Item list ---

@router.get("/", response_class=HTMLResponse)
def item_list(
    request: Request,
    q: str = Query("", alias="q"),
    location: Optional[int] = Query(None),
    show_deleted: bool = Query(False),
    db: Session = Depends(get_db),
):
    locations = db.query(StorageLocation).order_by(StorageLocation.name).all()

    # Subquery: latest revision per item
    latest_rev_sq = (
        db.query(
            ItemRevision.item_id,
            func.max(ItemRevision.revision_num).label("max_rev"),
        )
        .group_by(ItemRevision.item_id)
        .subquery()
    )

    query = (
        db.query(ItemRevision)
        .join(
            latest_rev_sq,
            (ItemRevision.item_id == latest_rev_sq.c.item_id)
            & (ItemRevision.revision_num == latest_rev_sq.c.max_rev),
        )
        .options(
            joinedload(ItemRevision.item),
            joinedload(ItemRevision.storage_location),
        )
    )

    if not show_deleted:
        query = query.filter(ItemRevision.is_deleted == False)  # noqa: E712
    if q:
        query = query.filter(ItemRevision.name.ilike(f"%{q}%"))
    if location:
        query = query.filter(ItemRevision.storage_location_id == location)

    revisions = query.order_by(ItemRevision.expiration_date.asc()).all()

    # Group revisions by location for carousel layout
    loc_items = {loc.id: [] for loc in locations}
    for rev in revisions:
        if rev.storage_location_id in loc_items:
            loc_items[rev.storage_location_id].append(rev)

    sections = []
    for loc in locations:
        items = loc_items.get(loc.id, [])
        if items:
            sections.append({"location": loc, "revisions": items})

    # Build tag sections for default tags
    default_tags = db.query(Tag).filter(Tag.is_default == True).order_by(Tag.name).all()  # noqa: E712
    tag_sections = []
    # Build a lookup of item_id -> latest revision from our query results
    rev_by_item = {rev.item_id: rev for rev in revisions}
    for tag in default_tags:
        # Get items with this tag that have a latest revision in our results
        tag_revs = []
        for item in tag.items:
            if item.id in rev_by_item:
                tag_revs.append(rev_by_item[item.id])
        if tag_revs:
            tag_revs.sort(key=lambda r: r.expiration_date or date.max)
            tag_sections.append({"tag": tag, "revisions": tag_revs})

    # Build "Expiring Soon" section (within 3 days, not already expired)
    today_ = date.today()
    soon = today_ + timedelta(days=3)
    expiring_soon = [
        rev for rev in revisions
        if rev.expiration_date
        and not rev.is_deleted
        and today_ <= rev.expiration_date <= soon
    ]

    return templates.TemplateResponse(
        "items/list.html",
        {
            "request": request,
            "sections": sections,
            "tag_sections": tag_sections,
            "expiring_soon": expiring_soon,
            "locations": locations,
            "q": q,
            "show_deleted": show_deleted,
            "today": today_,
            "photo_url": photo_url,
        },
    )


# --- Create item ---

@router.get("/items/new", response_class=HTMLResponse)
def create_form(
    request: Request,
    location: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    locations = db.query(StorageLocation).order_by(StorageLocation.name).all()
    all_tags = db.query(Tag).order_by(Tag.name).all()
    return templates.TemplateResponse(
        "items/create.html",
        {
            "request": request,
            "locations": locations,
            "all_tags": all_tags,
            "today": date.today(),
            "default_exp": date.today() + timedelta(days=7),
            "preselect_location": location,
        },
    )


@router.post("/items")
async def create_item(
    request: Request,
    name: str = Form(...),
    date_prepared: date = Form(...),
    expiration_date: Optional[date] = Form(None),
    storage_location_id: int = Form(...),
    link_urls: List[str] = Form(default=[]),
    link_labels: List[str] = Form(default=[]),
    notes: str = Form(""),
    amount: Optional[float] = Form(None),
    amount_unit: str = Form(""),
    tag_ids: List[int] = Form(default=[]),
    photo: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    errors = []
    if not name.strip():
        errors.append("Name is required.")

    exp = expiration_date or (date_prepared + timedelta(days=7))
    if exp < date_prepared:
        errors.append("Expiration date must be on or after date prepared.")

    # Validate links
    clean_links = []
    for url, label in zip(link_urls, link_labels):
        url = url.strip()
        if not url:
            continue
        if not URL_RE.match(url):
            errors.append(f"Invalid URL: {url}")
        else:
            clean_links.append((url, label.strip() or None))

    photo_filename = None
    if photo and photo.filename:
        try:
            photo_filename = await save_photo(photo)
        except ValueError as e:
            errors.append(str(e))

    if errors:
        locations = db.query(StorageLocation).order_by(StorageLocation.name).all()
        all_tags = db.query(Tag).order_by(Tag.name).all()
        return templates.TemplateResponse(
            "items/create.html",
            {
                "request": request,
                "locations": locations,
                "all_tags": all_tags,
                "today": date.today(),
                "default_exp": exp,
                "errors": errors,
                "form": {
                    "name": name,
                    "date_prepared": date_prepared,
                    "expiration_date": expiration_date,
                    "storage_location_id": storage_location_id,
                    "notes": notes,
                    "amount": amount,
                    "amount_unit": amount_unit,
                    "tag_ids": tag_ids,
                },
            },
        )

    item = FoodItem()
    db.add(item)
    db.flush()  # get item.id

    # Associate tags
    if tag_ids:
        tags = db.query(Tag).filter(Tag.id.in_(tag_ids)).all()
        item.tags = tags

    revision = ItemRevision(
        item_id=item.id,
        revision_num=1,
        name=name.strip(),
        date_prepared=date_prepared,
        expiration_date=exp,
        storage_location_id=storage_location_id,
        photo_filename=photo_filename,
        notes=notes.strip() or None,
        amount=amount,
        amount_unit=amount_unit.strip() or None,
    )
    db.add(revision)
    db.flush()

    for url, label in clean_links:
        db.add(RevisionLink(revision_id=revision.id, url=url, label=label))

    db.commit()
    return RedirectResponse(f"/i/{item.public_id}", status_code=303)


# --- Item detail ---

@router.get("/i/{public_id}", response_class=HTMLResponse)
def item_detail(public_id: str, request: Request, db: Session = Depends(get_db)):
    item = _get_item_or_404(public_id, db)
    rev = item.latest_revision

    return templates.TemplateResponse(
        "items/detail.html",
        {
            "request": request,
            "item": item,
            "rev": rev,
            "is_deleted": item.is_deleted,
            "photo_url": photo_url,
            "item_url": item_url(public_id),
            "today": date.today(),
        },
    )


# --- QR code image ---

@router.get("/i/{public_id}/qr.png")
def qr_image(public_id: str, db: Session = Depends(get_db)):
    item = _get_item_or_404(public_id, db)
    png = generate_qr_png(item.public_id)
    return Response(content=png, media_type="image/png")


# --- Printable label ---

@router.get("/i/{public_id}/label", response_class=HTMLResponse)
def printable_label(public_id: str, request: Request, db: Session = Depends(get_db)):
    item = _get_item_or_404(public_id, db)
    rev = item.latest_revision

    return templates.TemplateResponse(
        "items/label.html",
        {
            "request": request,
            "item": item,
            "rev": rev,
            "qr_url": f"/i/{public_id}/qr.png",
        },
    )


# --- Edit item ---

@router.get("/i/{public_id}/edit", response_class=HTMLResponse)
def edit_form(public_id: str, request: Request, db: Session = Depends(get_db)):
    item = _get_item_or_404(public_id, db)
    rev = item.latest_active_revision or item.latest_revision
    locations = db.query(StorageLocation).order_by(StorageLocation.name).all()
    all_tags = db.query(Tag).order_by(Tag.name).all()
    item_tag_ids = {t.id for t in item.tags}

    return templates.TemplateResponse(
        "items/edit.html",
        {
            "request": request,
            "item": item,
            "rev": rev,
            "locations": locations,
            "all_tags": all_tags,
            "item_tag_ids": item_tag_ids,
            "photo_url": photo_url,
        },
    )


@router.post("/i/{public_id}/edit")
async def save_edit(
    public_id: str,
    request: Request,
    name: str = Form(...),
    date_prepared: date = Form(...),
    expiration_date: Optional[date] = Form(None),
    storage_location_id: int = Form(...),
    link_urls: List[str] = Form(default=[]),
    link_labels: List[str] = Form(default=[]),
    notes: str = Form(""),
    amount: Optional[float] = Form(None),
    amount_unit: str = Form(""),
    tag_ids: List[int] = Form(default=[]),
    photo: Optional[UploadFile] = File(None),
    keep_photo: bool = Form(False),
    db: Session = Depends(get_db),
):
    item = _get_item_or_404(public_id, db)
    prev_rev = item.latest_revision

    errors = []
    if not name.strip():
        errors.append("Name is required.")

    exp = expiration_date or (date_prepared + timedelta(days=7))
    if exp < date_prepared:
        errors.append("Expiration date must be on or after date prepared.")

    clean_links = []
    for url, label in zip(link_urls, link_labels):
        url = url.strip()
        if not url:
            continue
        if not URL_RE.match(url):
            errors.append(f"Invalid URL: {url}")
        else:
            clean_links.append((url, label.strip() or None))

    photo_filename = None
    if photo and photo.filename:
        try:
            photo_filename = await save_photo(photo)
        except ValueError as e:
            errors.append(str(e))
    elif keep_photo and prev_rev:
        photo_filename = prev_rev.photo_filename

    if errors:
        locations = db.query(StorageLocation).order_by(StorageLocation.name).all()
        all_tags = db.query(Tag).order_by(Tag.name).all()
        return templates.TemplateResponse(
            "items/edit.html",
            {
                "request": request,
                "item": item,
                "rev": prev_rev,
                "locations": locations,
                "all_tags": all_tags,
                "item_tag_ids": set(tag_ids),
                "errors": errors,
                "photo_url": photo_url,
            },
        )

    # Update item tags
    if tag_ids:
        item.tags = db.query(Tag).filter(Tag.id.in_(tag_ids)).all()
    else:
        item.tags = []

    new_num = (prev_rev.revision_num + 1) if prev_rev else 1
    revision = ItemRevision(
        item_id=item.id,
        revision_num=new_num,
        name=name.strip(),
        date_prepared=date_prepared,
        expiration_date=exp,
        storage_location_id=storage_location_id,
        photo_filename=photo_filename,
        notes=notes.strip() or None,
        amount=amount,
        amount_unit=amount_unit.strip() or None,
    )
    db.add(revision)
    db.flush()

    for url, label in clean_links:
        db.add(RevisionLink(revision_id=revision.id, url=url, label=label))

    db.commit()
    return RedirectResponse(f"/i/{public_id}", status_code=303)


# --- Quick update amount ---

@router.post("/i/{public_id}/amount")
def update_amount(
    public_id: str,
    amount: Optional[float] = Form(None),
    amount_unit: str = Form(""),
    db: Session = Depends(get_db),
):
    item = _get_item_or_404(public_id, db)
    prev = item.latest_revision

    revision = ItemRevision(
        item_id=item.id,
        revision_num=(prev.revision_num + 1) if prev else 1,
        name=prev.name if prev else "Unknown",
        date_prepared=prev.date_prepared if prev else date.today(),
        expiration_date=prev.expiration_date if prev else None,
        storage_location_id=prev.storage_location_id if prev else 1,
        photo_filename=prev.photo_filename if prev else None,
        notes=prev.notes if prev else None,
        amount=amount,
        amount_unit=amount_unit.strip() or None,
        is_deleted=prev.is_deleted if prev else False,
    )
    db.add(revision)
    db.flush()

    # Copy links from previous revision
    if prev:
        for link in prev.links:
            db.add(RevisionLink(
                revision_id=revision.id, url=link.url, label=link.label
            ))

    db.commit()
    return RedirectResponse("/", status_code=303)


# --- Delete ---

@router.post("/i/{public_id}/delete")
def soft_delete(public_id: str, db: Session = Depends(get_db)):
    item = _get_item_or_404(public_id, db)
    prev = item.latest_revision

    revision = ItemRevision(
        item_id=item.id,
        revision_num=(prev.revision_num + 1) if prev else 1,
        name=prev.name if prev else "Unknown",
        date_prepared=prev.date_prepared if prev else date.today(),
        expiration_date=prev.expiration_date if prev else None,
        storage_location_id=prev.storage_location_id if prev else 1,
        photo_filename=prev.photo_filename if prev else None,
        notes=prev.notes if prev else None,
        amount=prev.amount if prev else None,
        amount_unit=prev.amount_unit if prev else None,
        is_deleted=True,
    )
    db.add(revision)
    db.commit()
    return RedirectResponse(f"/i/{public_id}", status_code=303)


# --- Restore ---

@router.post("/i/{public_id}/restore")
def restore_item(public_id: str, db: Session = Depends(get_db)):
    item = _get_item_or_404(public_id, db)

    # Copy fields from last active revision, or from latest if none active
    source = item.latest_active_revision or item.latest_revision
    prev = item.latest_revision

    revision = ItemRevision(
        item_id=item.id,
        revision_num=(prev.revision_num + 1) if prev else 1,
        name=source.name,
        date_prepared=source.date_prepared,
        expiration_date=source.expiration_date,
        storage_location_id=source.storage_location_id,
        photo_filename=source.photo_filename,
        notes=source.notes,
        amount=source.amount,
        amount_unit=source.amount_unit,
        is_deleted=False,
    )
    db.add(revision)
    db.flush()

    # Copy links from source revision
    for link in source.links:
        db.add(RevisionLink(
            revision_id=revision.id, url=link.url, label=link.label
        ))

    db.commit()
    return RedirectResponse(f"/i/{public_id}", status_code=303)


# --- Reuse label ---

@router.get("/i/{public_id}/reuse", response_class=HTMLResponse)
def reuse_form(public_id: str, request: Request, db: Session = Depends(get_db)):
    item = _get_item_or_404(public_id, db)
    if not item.is_deleted:
        return RedirectResponse(f"/i/{public_id}", status_code=303)

    prev = item.latest_revision
    locations = db.query(StorageLocation).order_by(StorageLocation.name).all()

    return templates.TemplateResponse(
        "items/reuse.html",
        {
            "request": request,
            "public_id": public_id,
            "prev_name": prev.name if prev else "Unknown",
            "locations": locations,
            "today": date.today(),
            "default_exp": date.today() + timedelta(days=7),
        },
    )


@router.post("/i/{public_id}/reuse")
async def reuse_label(
    public_id: str,
    request: Request,
    name: str = Form(...),
    date_prepared: date = Form(...),
    expiration_date: Optional[date] = Form(None),
    storage_location_id: int = Form(...),
    link_urls: List[str] = Form(default=[]),
    link_labels: List[str] = Form(default=[]),
    notes: str = Form(""),
    amount: Optional[float] = Form(None),
    amount_unit: str = Form(""),
    photo: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    item = _get_item_or_404(public_id, db)
    prev = item.latest_revision

    errors = []
    if not name.strip():
        errors.append("Name is required.")

    exp = expiration_date or (date_prepared + timedelta(days=7))
    if exp < date_prepared:
        errors.append("Expiration date must be on or after date prepared.")

    clean_links = []
    for url, label in zip(link_urls, link_labels):
        url = url.strip()
        if not url:
            continue
        if not URL_RE.match(url):
            errors.append(f"Invalid URL: {url}")
        else:
            clean_links.append((url, label.strip() or None))

    photo_filename = None
    if photo and photo.filename:
        try:
            photo_filename = await save_photo(photo)
        except ValueError as e:
            errors.append(str(e))

    if errors:
        locations = db.query(StorageLocation).order_by(StorageLocation.name).all()
        return templates.TemplateResponse(
            "items/reuse.html",
            {
                "request": request,
                "public_id": public_id,
                "prev_name": prev.name if prev else "Unknown",
                "locations": locations,
                "today": date.today(),
                "default_exp": exp,
                "errors": errors,
                "form": {
                    "name": name,
                    "date_prepared": date_prepared,
                    "expiration_date": expiration_date,
                    "storage_location_id": storage_location_id,
                    "notes": notes,
                    "amount": amount,
                    "amount_unit": amount_unit,
                },
            },
        )

    new_num = (prev.revision_num + 1) if prev else 1
    revision = ItemRevision(
        item_id=item.id,
        revision_num=new_num,
        name=name.strip(),
        date_prepared=date_prepared,
        expiration_date=exp,
        storage_location_id=storage_location_id,
        photo_filename=photo_filename,
        notes=notes.strip() or None,
        amount=amount,
        amount_unit=amount_unit.strip() or None,
        is_deleted=False,
    )
    db.add(revision)
    db.flush()

    for url, label in clean_links:
        db.add(RevisionLink(revision_id=revision.id, url=url, label=label))

    db.commit()
    return RedirectResponse(f"/i/{public_id}", status_code=303)


# --- History ---

@router.get("/i/{public_id}/history", response_class=HTMLResponse)
def item_history(public_id: str, request: Request, db: Session = Depends(get_db)):
    item = _get_item_or_404(public_id, db)

    return templates.TemplateResponse(
        "items/history.html",
        {
            "request": request,
            "item": item,
            "revisions": list(reversed(item.revisions)),
            "photo_url": photo_url,
        },
    )
