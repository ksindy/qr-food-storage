import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./food_storage.db")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(BASE_DIR / "uploads")))

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
