import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, Form, Query, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from difflib import SequenceMatcher


def _fuzzy_match(query, choices, cutoff=0.75):
    """Find the best fuzzy match for query among choices. Returns (match, score) or None."""
    query_lower = query.lower()
    best = None
    best_score = 0.0
    for choice in choices:
        score = SequenceMatcher(None, query_lower, choice.lower()).ratio()
        if score >= cutoff and score > best_score:
            best = choice
            best_score = score
    return (best, best_score) if best else None

from app.config import GOOGLE_CLOUD_API_KEY
from app.database import get_db
from app.models import FoodItem, InventoryEntry, ItemRevision, RevisionLink, StorageLocation, Tag
from app.ocr import extract_text, get_name_candidates, guess_food_name
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
            joinedload(FoodItem.entries),
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
            joinedload(ItemRevision.item).joinedload(FoodItem.entries),
            joinedload(ItemRevision.storage_location),
        )
    )

    if not show_deleted:
        query = query.filter(ItemRevision.is_deleted == False)  # noqa: E712
    if q:
        query = query.filter(ItemRevision.name.ilike(f"%{q}%"))
    if location:
        query = query.filter(ItemRevision.storage_location_id == location)

    revisions = query.all()

    # Group revisions by location for carousel layout
    loc_items = {loc.id: [] for loc in locations}
    for rev in revisions:
        if rev.storage_location_id in loc_items:
            loc_items[rev.storage_location_id].append(rev)

    sections = []
    for loc in locations:
        items = loc_items.get(loc.id, [])
        if items:
            items.sort(key=lambda r: r.item.earliest_expiry or date.max)
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
            tag_revs.sort(key=lambda r: r.item.earliest_expiry or date.max)
            tag_sections.append({"tag": tag, "revisions": tag_revs})

    # Build "Expiring Soon" section (within 3 days, not already expired)
    today_ = date.today()
    soon = today_ + timedelta(days=3)
    expiring_soon = [
        rev for rev in revisions
        if not rev.is_deleted
        and rev.item.earliest_expiry
        and today_ <= rev.item.earliest_expiry <= soon
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
        expiration_date=None,
        storage_location_id=storage_location_id,
        photo_filename=photo_filename,
        notes=notes.strip() or None,
        amount=None,
        amount_unit=None,
    )
    db.add(revision)
    db.flush()

    for url, label in clean_links:
        db.add(RevisionLink(revision_id=revision.id, url=url, label=label))

    entry = InventoryEntry(
        item_id=item.id,
        date_prepared=date_prepared,
        expiration_date=exp,
        amount=amount if amount is not None else 1,
        amount_unit=amount_unit.strip() or "qty",
    )
    db.add(entry)

    db.commit()
    return RedirectResponse(f"/i/{item.public_id}", status_code=303)


# --- Bulk add ---

@router.get("/items/bulk", response_class=HTMLResponse)
def bulk_upload_form(request: Request, db: Session = Depends(get_db)):
    locations = db.query(StorageLocation).order_by(StorageLocation.name).all()
    return templates.TemplateResponse(
        "items/bulk_upload.html",
        {
            "request": request,
            "locations": locations,
            "api_key_set": bool(GOOGLE_CLOUD_API_KEY),
        },
    )


@router.post("/items/bulk", response_class=HTMLResponse)
async def bulk_upload_process(
    request: Request,
    storage_location_id: int = Form(...),
    photos: List[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
):
    if len(photos) > 6:
        photos = photos[:6]

    items = []
    has_photos = any(p.filename for p in photos)

    if not has_photos:
        # No photos — add one blank item for manual entry
        items.append({
            "filename": "",
            "photo_url": None,
            "name": "",
            "name_candidates": [],
            "raw_text": "",
            "grouped": False,
            "group_key": "",
        })

    for photo in photos:
        if not photo.filename:
            continue
        try:
            filename = await save_photo(photo)
        except ValueError:
            continue

        raw_text = extract_text(filename)
        candidates = get_name_candidates(raw_text)
        name = candidates[0] if candidates else ""

        items.append({
            "filename": filename,
            "photo_url": photo_url(filename),
            "name": name,
            "name_candidates": candidates,
            "raw_text": raw_text,
            "grouped": False,
        })

    # Auto-detect matching names for grouping
    name_counts = defaultdict(int)
    for item in items:
        if item["name"]:
            name_counts[item["name"].strip().lower()] += 1
    for item in items:
        key = item["name"].strip().lower() if item["name"] else ""
        if key and name_counts[key] > 1:
            item["grouped"] = True
            item["group_key"] = key
        else:
            item["group_key"] = ""

    # Fuzzy-match against existing non-deleted items
    latest_rev_sq = (
        db.query(
            ItemRevision.item_id,
            func.max(ItemRevision.revision_num).label("max_rev"),
        )
        .group_by(ItemRevision.item_id)
        .subquery()
    )
    existing_revs = (
        db.query(ItemRevision)
        .join(
            latest_rev_sq,
            (ItemRevision.item_id == latest_rev_sq.c.item_id)
            & (ItemRevision.revision_num == latest_rev_sq.c.max_rev),
        )
        .options(
            joinedload(ItemRevision.item).joinedload(FoodItem.entries),
        )
        .filter(ItemRevision.is_deleted == False)  # noqa: E712
        .all()
    )
    existing_choices = {
        rev.name: {
            "public_id": rev.item.public_id,
            "name": rev.name,
            "entry_count": len([e for e in rev.item.entries if not e.is_consumed]),
        }
        for rev in existing_revs
    }
    existing_names = list(existing_choices.keys())

    for item in items:
        item["existing_match"] = None
        if item["name"] and existing_names:
            result = _fuzzy_match(item["name"], existing_names)
            if result:
                matched_name = result[0]
                item["existing_match"] = existing_choices[matched_name]

    locations = db.query(StorageLocation).order_by(StorageLocation.name).all()
    today_ = date.today()
    return templates.TemplateResponse(
        "items/bulk_review.html",
        {
            "request": request,
            "items": items,
            "locations": locations,
            "default_location_id": storage_location_id,
            "today": today_,
            "default_exp": today_ + timedelta(days=7),
        },
    )


@router.post("/items/bulk/confirm")
async def bulk_confirm(request: Request, db: Session = Depends(get_db)):
    form = await request.form()

    # Parse indexed form fields
    items_data = []
    i = 0
    while True:
        name = form.get(f"items[{i}].name")
        if name is None:
            break
        items_data.append({
            "name": name.strip(),
            "filename": form.get(f"items[{i}].filename", ""),
            "date_prepared": form.get(f"items[{i}].date_prepared", ""),
            "expiration_date": form.get(f"items[{i}].expiration_date", ""),
            "storage_location_id": int(form.get(f"items[{i}].storage_location_id", "1")),
            "amount": form.get(f"items[{i}].amount", "1"),
            "amount_unit": form.get(f"items[{i}].amount_unit", "qty"),
            "group_key": form.get(f"items[{i}].group_key", ""),
            "existing_id": form.get(f"items[{i}].existing_id", ""),
        })
        i += 1

    if not items_data:
        return RedirectResponse("/items/bulk", status_code=303)

    # Separate items destined for existing DB items vs new ones
    existing_items = []
    new_items = []
    for item in items_data:
        if item["existing_id"]:
            existing_items.append(item)
        else:
            new_items.append(item)

    # Group new items by group_key
    grouped = defaultdict(list)
    ungrouped = []
    for item in new_items:
        if item["group_key"]:
            grouped[item["group_key"]].append(item)
        else:
            ungrouped.append(item)

    today_ = date.today()

    def _parse_date(val, fallback):
        if not val:
            return fallback
        try:
            return date.fromisoformat(val)
        except ValueError:
            return fallback

    def _create_food_item(item_data_list):
        """Create one FoodItem with one revision and N inventory entries."""
        first = item_data_list[0]
        food_item = FoodItem()
        db.add(food_item)
        db.flush()

        dp = _parse_date(first["date_prepared"], today_)
        revision = ItemRevision(
            item_id=food_item.id,
            revision_num=1,
            name=first["name"] or "Unknown",
            date_prepared=dp,
            expiration_date=None,
            storage_location_id=first["storage_location_id"],
            photo_filename=first["filename"] or None,
            notes=None,
            amount=None,
            amount_unit=None,
        )
        db.add(revision)

        for item_d in item_data_list:
            idp = _parse_date(item_d["date_prepared"], today_)
            iexp = _parse_date(item_d["expiration_date"], idp + timedelta(days=7))
            try:
                amt = float(item_d["amount"]) if item_d["amount"] else 1
            except ValueError:
                amt = 1
            entry = InventoryEntry(
                item_id=food_item.id,
                date_prepared=idp,
                expiration_date=iexp,
                amount=amt,
                amount_unit=item_d["amount_unit"].strip() or "qty",
            )
            db.add(entry)

    # Add entries to existing items
    for item_d in existing_items:
        food_item = (
            db.query(FoodItem)
            .filter(FoodItem.public_id == item_d["existing_id"])
            .first()
        )
        if not food_item:
            continue
        idp = _parse_date(item_d["date_prepared"], today_)
        iexp = _parse_date(item_d["expiration_date"], idp + timedelta(days=7))
        try:
            amt = float(item_d["amount"]) if item_d["amount"] else 1
        except ValueError:
            amt = 1
        entry = InventoryEntry(
            item_id=food_item.id,
            date_prepared=idp,
            expiration_date=iexp,
            amount=amt,
            amount_unit=item_d["amount_unit"].strip() or "qty",
        )
        db.add(entry)

    # Create grouped items (one FoodItem per group)
    for group_items in grouped.values():
        _create_food_item(group_items)

    # Create ungrouped items (one FoodItem each)
    for item in ungrouped:
        _create_food_item([item])

    db.commit()
    return RedirectResponse("/", status_code=303)


@router.get("/api/match-item")
def match_item(q: str = Query(""), db: Session = Depends(get_db)):
    """Return the best fuzzy match for a name against existing non-deleted items."""
    q = q.strip()
    if not q:
        return {"match": None}

    latest_rev_sq = (
        db.query(
            ItemRevision.item_id,
            func.max(ItemRevision.revision_num).label("max_rev"),
        )
        .group_by(ItemRevision.item_id)
        .subquery()
    )
    existing_revs = (
        db.query(ItemRevision)
        .join(
            latest_rev_sq,
            (ItemRevision.item_id == latest_rev_sq.c.item_id)
            & (ItemRevision.revision_num == latest_rev_sq.c.max_rev),
        )
        .options(
            joinedload(ItemRevision.item).joinedload(FoodItem.entries),
        )
        .filter(ItemRevision.is_deleted == False)  # noqa: E712
        .all()
    )

    choices = {}
    for rev in existing_revs:
        choices[rev.name] = {
            "public_id": rev.item.public_id,
            "name": rev.name,
            "entry_count": len([e for e in rev.item.entries if not e.is_consumed]),
        }

    if not choices:
        return {"match": None}

    result = _fuzzy_match(q, list(choices.keys()))
    if result:
        return {"match": choices[result[0]]}
    return {"match": None}


# --- Item detail ---

@router.get("/i/{public_id}", response_class=HTMLResponse)
def item_detail(public_id: str, request: Request, db: Session = Depends(get_db)):
    item = _get_item_or_404(public_id, db)
    rev = item.latest_revision
    today_ = date.today()

    return templates.TemplateResponse(
        "items/detail.html",
        {
            "request": request,
            "item": item,
            "rev": rev,
            "is_deleted": item.is_deleted,
            "entries": item.active_entries,
            "consumed_count": len([e for e in item.entries if e.is_consumed]),
            "photo_url": photo_url,
            "item_url": item_url(public_id),
            "today": today_,
            "default_exp": today_ + timedelta(days=7),
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
            "earliest_expiry": item.earliest_expiry,
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
    storage_location_id: int = Form(...),
    link_urls: List[str] = Form(default=[]),
    link_labels: List[str] = Form(default=[]),
    notes: str = Form(""),
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
        date_prepared=prev_rev.date_prepared if prev_rev else date.today(),
        expiration_date=None,
        storage_location_id=storage_location_id,
        photo_filename=photo_filename,
        notes=notes.strip() or None,
        amount=None,
        amount_unit=None,
    )
    db.add(revision)
    db.flush()

    for url, label in clean_links:
        db.add(RevisionLink(revision_id=revision.id, url=url, label=label))

    db.commit()
    return RedirectResponse(f"/i/{public_id}", status_code=303)


# --- Inventory entries ---

@router.post("/i/{public_id}/entries")
def add_entry(
    public_id: str,
    date_prepared: date = Form(...),
    expiration_date: Optional[date] = Form(None),
    amount: Optional[float] = Form(None),
    amount_unit: str = Form(""),
    db: Session = Depends(get_db),
):
    item = _get_item_or_404(public_id, db)
    exp = expiration_date or (date_prepared + timedelta(days=7))
    entry = InventoryEntry(
        item_id=item.id,
        date_prepared=date_prepared,
        expiration_date=exp,
        amount=amount if amount is not None else 1,
        amount_unit=amount_unit.strip() or "qty",
    )
    db.add(entry)
    db.commit()
    return RedirectResponse(f"/i/{public_id}", status_code=303)


@router.post("/i/{public_id}/entries/{entry_id}/consume")
def consume_entry(
    public_id: str,
    entry_id: int,
    db: Session = Depends(get_db),
):
    from datetime import datetime, timezone
    entry = db.query(InventoryEntry).filter(
        InventoryEntry.id == entry_id,
        InventoryEntry.item_id == FoodItem.id,
        FoodItem.public_id == public_id,
    ).join(FoodItem).first()
    if not entry:
        raise _not_found()
    entry.is_consumed = True
    entry.consumed_at = datetime.now(timezone.utc)
    db.commit()
    return RedirectResponse(f"/i/{public_id}", status_code=303)


# --- Delete ---

@router.post("/i/{public_id}/delete")
def soft_delete(public_id: str, db: Session = Depends(get_db)):
    from datetime import datetime, timezone
    item = _get_item_or_404(public_id, db)
    prev = item.latest_revision

    revision = ItemRevision(
        item_id=item.id,
        revision_num=(prev.revision_num + 1) if prev else 1,
        name=prev.name if prev else "Unknown",
        date_prepared=prev.date_prepared if prev else date.today(),
        expiration_date=None,
        storage_location_id=prev.storage_location_id if prev else 1,
        photo_filename=prev.photo_filename if prev else None,
        notes=prev.notes if prev else None,
        amount=None,
        amount_unit=None,
        is_deleted=True,
    )
    db.add(revision)

    # Mark all active entries as consumed
    now = datetime.now(timezone.utc)
    for entry in item.active_entries:
        entry.is_consumed = True
        entry.consumed_at = now

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
        expiration_date=None,
        storage_location_id=source.storage_location_id,
        photo_filename=source.photo_filename,
        notes=source.notes,
        amount=None,
        amount_unit=None,
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
        expiration_date=None,
        storage_location_id=storage_location_id,
        photo_filename=photo_filename,
        notes=notes.strip() or None,
        amount=None,
        amount_unit=None,
        is_deleted=False,
    )
    db.add(revision)
    db.flush()

    for url, label in clean_links:
        db.add(RevisionLink(revision_id=revision.id, url=url, label=label))

    entry = InventoryEntry(
        item_id=item.id,
        date_prepared=date_prepared,
        expiration_date=exp,
        amount=amount if amount is not None else 1,
        amount_unit=amount_unit.strip() or "qty",
    )
    db.add(entry)

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
