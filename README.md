# QR Food Storage

A mobile-friendly web app for labeling household food containers with QR codes. Create items, attach metadata and photos, generate printable 1"x1" labels, and scan QR codes to instantly view item details.

## Features

- **QR Code Labels** — Generate and print 1"x1" labels with QR codes for any food container
- **Scan to View** — Scan a QR code to open the item's detail page with all metadata + photo
- **Version History** — Every edit creates a new revision; nothing is ever overwritten
- **Soft Delete** — Deleted items are hidden but retained; scan a deleted label to restore it
- **Storage Locations** — Organize by Pantry, Fridge, Freezer, or custom locations you add
- **Links** — Attach recipe URLs, Google Drive links, or PDFs to any item
- **Photo Support** — Upload a photo per item (stored locally; structured for cloud swap)
- **Search & Filter** — Search by name, filter by location, toggle deleted items

## Quick Start

```bash
# 1. Clone and enter the project
cd qr_food_storage

# 2. Create a virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. (Optional) Copy and edit environment config
cp .env.example .env
# Edit .env to set BASE_URL if not localhost:8000

# 4. Run the app
python run.py
```

Open **http://localhost:8000** in your browser. The database and default storage locations (Pantry, Fridge, Freezer) are created automatically on first run.

## How QR URLs Work

Each food item gets a unique, non-guessable public ID (12-character URL-safe token). The QR code encodes:

```
{BASE_URL}/i/{public_id}
```

For example: `http://localhost:8000/i/jdwE2QXO5-GI`

When scanned, this URL loads the item's detail page showing:
- Name, dates, storage location
- Photo (if uploaded)
- Links (recipes, PDFs, etc.)
- Actions: Edit, Delete, View History

The `BASE_URL` is configured via the `.env` file. For production, set it to your public domain (e.g., `https://food.example.com`).

## Printing 1"x1" Labels

1. Create an item, then click **"Print label"** on the item detail page (or visit `/i/{public_id}/label`)
2. Click the **"Print Label"** button on the label page
3. In your browser's print dialog:
   - **Scale: 100%** (do NOT use "Fit to page" or "Shrink to fit")
   - **Paper size:** Use your label sheet size, or set "Custom" to 1" x 1"
   - **Margins: None** or Minimum
4. The label includes the QR code, item name, and expiration date

**Tips for label sheets:**
- Works great with 1"x1" square label sheets (e.g., Avery or similar)
- For regular paper, the label prints as a 1"x1" square in the top-left corner; cut to size
- Test with one label first to verify sizing on your printer

## Project Structure

```
qr_food_storage/
├── app/
│   ├── __init__.py
│   ├── config.py          # Environment config (BASE_URL, DB, etc.)
│   ├── database.py         # SQLAlchemy engine + session
│   ├── main.py             # FastAPI app setup + startup
│   ├── models.py           # SQLAlchemy models
│   ├── photo.py            # Photo upload abstraction
│   ├── qr.py               # QR code generation
│   ├── seed.py             # Default storage locations
│   ├── templating.py       # Jinja2 template config
│   ├── routes/
│   │   ├── items.py        # All item CRUD + QR + label routes
│   │   └── locations.py    # Storage location management
│   ├── static/css/
│   │   └── app.css         # Custom styles
│   └── templates/
│       ├── base.html       # Base layout (Pico CSS)
│       ├── items/
│       │   ├── list.html   # Item listing with search/filter
│       │   ├── create.html # New item form
│       │   ├── detail.html # Item detail (QR landing page)
│       │   ├── edit.html   # Edit form
│       │   ├── history.html# Revision timeline
│       │   └── label.html  # Printable 1"x1" label
│       └── locations/
│           └── list.html   # Manage storage locations
├── uploads/                # Photo uploads (gitignored)
├── alembic/                # Database migrations
├── requirements.txt
├── run.py                  # Entry point
├── .env.example
└── README.md
```

## Data Model

- **food_items** — Stable identity with `public_id` (used in QR code URL)
- **item_revisions** — Append-only revision history per item (name, dates, location, photo, is_deleted)
- **revision_links** — 0..N URLs per revision (recipes, PDFs, Google Drive links)
- **storage_locations** — User-extendable list (seeded with Pantry, Fridge, Freezer)

## Configuration

All configuration is via environment variables (or `.env` file):

| Variable | Default | Description |
|---|---|---|
| `BASE_URL` | `http://localhost:8000` | Base URL encoded in QR codes |
| `DATABASE_URL` | `sqlite:///./food_storage.db` | Database connection string |
| `SECRET_KEY` | `dev-secret-change-me` | Session signing key |
| `UPLOAD_DIR` | `./uploads` | Photo upload directory |

## Routes

| Method | Route | Description |
|---|---|---|
| GET | `/` | Items list (search, filter, sort by expiration) |
| GET | `/items/new` | Create item form |
| POST | `/items` | Create item + first revision |
| GET | `/i/{id}` | Item detail (QR landing page) |
| GET | `/i/{id}/edit` | Edit form |
| POST | `/i/{id}/edit` | Save edit (new revision) |
| POST | `/i/{id}/delete` | Soft delete |
| POST | `/i/{id}/restore` | Restore deleted item |
| GET | `/i/{id}/history` | Revision timeline |
| GET | `/i/{id}/label` | Printable 1"x1" label |
| GET | `/i/{id}/qr.png` | QR code PNG image |
| GET | `/locations` | Manage storage locations |
| POST | `/locations` | Add new location |

## Manual Test Checklist

- [ ] Home page loads with empty state and "Create one" link
- [ ] Create item with name, dates, location, link, and photo
- [ ] Item detail page shows all metadata, photo, links, and QR code
- [ ] QR code image loads and encodes the correct URL
- [ ] Printable label page renders 1"x1" with QR + name + expiration
- [ ] Print preview shows correct 1"x1" sizing at 100% scale
- [ ] Edit an item; verify new revision appears in history
- [ ] "Keep current photo" checkbox preserves photo across edits
- [ ] Delete an item; verify "Deleted" banner on detail page
- [ ] Deleted items hidden from list by default; visible with "Show deleted" toggle
- [ ] Restore a deleted item; verify it reappears in list
- [ ] History page shows all revisions with timestamps
- [ ] Search by name filters the list
- [ ] Filter by storage location works
- [ ] Add a new storage location; appears in create/edit dropdowns
- [ ] Validation: expiration date before prepared date shows error
- [ ] Validation: invalid URL in links shows error

## Adding Authentication (Future)

This MVP has no authentication. To add simple household auth:

1. Install `passlib[bcrypt]` and add a `users` table
2. Add login/logout routes using FastAPI's session middleware (already included)
3. Create a dependency that checks `request.session.get("user_id")` and redirects to `/login` if missing
4. Add the dependency to the router: `router = APIRouter(dependencies=[Depends(require_auth)])`

Alternatively, use HTTP Basic Auth for the simplest possible protection:
```python
from fastapi.security import HTTPBasic, HTTPBasicCredentials
security = HTTPBasic()
# Add as a dependency to protected routes
```

## Deployment (Fly.io)

The app is deployed on Fly.io with SQLite on a persistent volume. The free tier is more than enough for a household app.

### First-Time Setup

```bash
# 1. Install the Fly CLI: https://fly.io/docs/flyctl/install/
# 2. Log in
fly auth login

# 3. Create the app (already configured in fly.toml)
fly apps create qr-food-storage

# 4. Create a persistent volume for the database + uploads
fly volumes create data --region sjc --size 1

# 5. Set secrets
fly secrets set SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')" \
               BASE_URL="https://qr-food-storage.fly.dev"

# 6. Deploy
fly deploy
```

The app will be live at **https://qr-food-storage.fly.dev**.

### Subsequent Deploys

```bash
fly deploy
```

The persistent volume survives redeploys — your database and photos are safe.

### Dev vs Production Workflow

| | Local Dev | Production |
|---|---|---|
| **Branch** | `dev` | `main` |
| **Run** | `python run.py` | `fly deploy` |
| **Database** | `./food_storage.db` | `/data/food_storage.db` (volume) |
| **Uploads** | `./uploads/` | `/data/uploads/` (volume) |
| **URL** | `http://localhost:8000` | `https://qr-food-storage.fly.dev` |

Workflow:
1. Work on `dev` branch locally, test with `python run.py`
2. When ready, merge to `main` and run `fly deploy`

### Useful Fly.io Commands

```bash
fly status              # App status and VM info
fly logs                # Stream production logs
fly ssh console         # SSH into the running VM
fly volumes list        # Check persistent volume
```
