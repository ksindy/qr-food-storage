"""Photo storage abstraction. Swap this module to use cloud storage later."""
import uuid
from pathlib import Path
from typing import Optional

from fastapi import UploadFile

from app.config import UPLOAD_DIR

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB


async def save_photo(file: UploadFile) -> str:
    """Save an uploaded photo and return the filename."""
    ext = Path(file.filename).suffix.lower() if file.filename else ".jpg"
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"File type {ext} not allowed. Use: {ALLOWED_EXTENSIONS}")

    filename = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_DIR / filename

    content = await file.read()
    if len(content) > MAX_SIZE_BYTES:
        raise ValueError("File too large (max 10 MB)")

    dest.write_bytes(content)
    return filename


def photo_url(filename: Optional[str]) -> Optional[str]:
    if not filename:
        return None
    return f"/uploads/{filename}"
