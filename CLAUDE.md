# QR Food Storage

QR-labeled food storage tracker. Scan a QR code on a container to see what's inside, when it was prepared, and when it expires.

## Stack

- **Backend:** FastAPI + Jinja2 (server-rendered, no JS build step)
- **Database:** SQLite + SQLAlchemy 2.0 (mapped_column style)
- **Styling:** Pico CSS
- **QR codes:** `qrcode[pil]`
- **Deploy:** Fly.io (`shared-cpu-1x`, 256MB RAM, 1 gunicorn worker)

## Run locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

No env vars needed for local dev — defaults are set in `app/config.py`.

## Deploy

```bash
fly deploy
```

SQLite DB and uploads live on a Fly persistent volume at `/data/`. Schema migrations run in `fly-entrypoint.sh` before the app starts.

## Architecture

- **Revision-based history:** `food_items` (stable identity) + `item_revisions` (append-only). Every edit creates a new revision.
- **Soft delete:** A revision with `is_deleted=True`. Restore creates a new non-deleted revision.
- **Public IDs:** 12-char URL-safe tokens (`secrets.token_urlsafe(9)`) used in URLs and QR codes.
- **Photos:** Stored on disk (`app/photo.py`), abstracted for future cloud swap.

## Constraints

- Python 3.9 compatibility: use `Optional[X]` and `List[X]` from `typing`, not `X | None` syntax.
- Keep it simple. This is a personal utility app — no over-engineering.
