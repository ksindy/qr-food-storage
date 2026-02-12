"""QR code generation."""
import io
import qrcode
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.moduledrawers import RoundedModuleDrawer

from app.config import BASE_URL


def item_url(public_id: str) -> str:
    return f"{BASE_URL}/i/{public_id}"


def generate_qr_png(public_id: str) -> bytes:
    """Generate a QR code PNG for the given public_id."""
    url = item_url(public_id)
    qr = qrcode.QRCode(
        version=None,  # auto-size
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(
        image_factory=StyledPilImage,
        module_drawer=RoundedModuleDrawer(),
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
