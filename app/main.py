from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import BASE_DIR, SECRET_KEY, UPLOAD_DIR
from app.database import Base, engine, SessionLocal
from app.seed import seed_locations, seed_tags

app = FastAPI(title="QR Food Storage")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_locations(db)
        seed_tags(db)
    finally:
        db.close()


# Register route modules
from app.routes import items, locations  # noqa: E402

app.include_router(items.router)
app.include_router(locations.router)
