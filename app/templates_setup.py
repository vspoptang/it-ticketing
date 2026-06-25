"""Shared Jinja2Templates instance — use this ONE instance everywhere."""

import re
from datetime import datetime, timezone

from markupsafe import Markup, escape
from starlette.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")


def _to_naive(value):
    """Convert a datetime to naive (strip tzinfo) for safe comparison."""
    if value is None:
        return None
    if hasattr(value, "tzinfo") and value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value


# Pattern: allow only <img src="data:image/..."> with safe attributes
_IMG_RE = re.compile(
    r'<img\s+src="data:image/(?:png|jpeg|jpg|gif|webp);base64,[A-Za-z0-9+/=]+"\s*(?:style="[^"]*")?\s*/?>',
    re.IGNORECASE,
)


def _safe_images(value):
    """Escape HTML but preserve pasted base64 image tags."""
    if value is None:
        return ""
    # First extract all valid img tags
    imgs = _IMG_RE.findall(value)
    # Escape everything
    safe = escape(value)
    # Put back valid img tags (they were escaped by escape())
    for img in imgs:
        escaped_img = escape(img)
        safe = safe.replace(escaped_img, img)
    return Markup(safe)


templates.env.filters["to_naive"] = _to_naive
templates.env.filters["safe_images"] = _safe_images
