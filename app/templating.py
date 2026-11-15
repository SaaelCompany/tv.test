from datetime import datetime

from fastapi.templating import Jinja2Templates

from .database import SessionLocal
from .site_content import get_site, site_view

templates = Jinja2Templates(directory="templates")


def render(request, name: str, context: dict | None = None):
    ctx = {"request": request, "now_year": datetime.utcnow().year}
    if context:
        ctx.update(context)
    db = SessionLocal()
    try:
        ctx["site"] = site_view(get_site(db))
    finally:
        db.close()
    return templates.TemplateResponse(name, ctx)
