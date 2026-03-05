"""Google Cloud Vision OCR for reading food labels."""
import base64
import io
import re
from typing import Optional

import requests
from PIL import Image

from app.config import GOOGLE_CLOUD_API_KEY, UPLOAD_DIR

VISION_URL = "https://vision.googleapis.com/v1/images:annotate"
MAX_DIMENSION = 1500

# Lines matching these patterns are noise, not food names
NOISE_PATTERNS = [
    re.compile(r"^\d+$"),  # pure numbers
    re.compile(r"\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}"),  # dates
    re.compile(r"^(best|use|sell)\s*(by|before|thru)", re.IGNORECASE),
    re.compile(r"^(exp|bb|mfg|lot|upc|sku)\b", re.IGNORECASE),
    re.compile(r"^\d{8,}$"),  # barcodes
    re.compile(r"^net\s*w", re.IGNORECASE),  # net weight
    re.compile(r"^\d+\s*(oz|lb|g|kg|ml|fl)\b", re.IGNORECASE),  # weights
]


def _resize_image_bytes(filepath: str) -> bytes:
    """Read image from disk, resize to max dimension, return JPEG bytes."""
    img = Image.open(filepath)
    img.thumbnail((MAX_DIMENSION, MAX_DIMENSION))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def extract_text(filename: str) -> str:
    """Send image to Google Cloud Vision TEXT_DETECTION and return detected text."""
    if not GOOGLE_CLOUD_API_KEY:
        return ""

    filepath = UPLOAD_DIR / filename
    if not filepath.exists():
        return ""

    image_bytes = _resize_image_bytes(str(filepath))
    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    payload = {
        "requests": [
            {
                "image": {"content": b64_image},
                "features": [{"type": "TEXT_DETECTION", "maxResults": 1}],
            }
        ]
    }

    try:
        resp = requests.post(
            VISION_URL,
            params={"key": GOOGLE_CLOUD_API_KEY},
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return ""

    data = resp.json()
    responses = data.get("responses", [])
    if not responses:
        return ""

    annotations = responses[0].get("fullTextAnnotation", {})
    return annotations.get("text", "")


def guess_food_name(raw_text: str) -> str:
    """Extract the most likely food name from OCR text."""
    if not raw_text:
        return ""

    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if len(line) < 2:
            continue
        if any(p.search(line) for p in NOISE_PATTERNS):
            continue
        # Return first non-noise line, title-cased
        return line.title()[:100]

    return ""
