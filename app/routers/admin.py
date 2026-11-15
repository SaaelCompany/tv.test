from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Plan
from ..site_content import get_site
from ..templating import templates

router = APIRouter(prefix="/admin")

ADMIN_COOKIE = "admin_token"


def _check(token: Optional[str]):
    if not settings.admin_password or token != settings.admin_password:
        raise HTTPException(status_code=401, detail="unauthorized")


@router.get("", response_class=HTMLResponse)
def admin_page(
    request: Request,
    admin_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    if admin_token != settings.admin_password or not settings.admin_password:
        return HTMLResponse(
            '<form method="post" action="/admin/login" style="max-width:320px;margin:80px auto;font-family:sans-serif">'
            '<h2>Admin</h2>'
            '<input type="password" name="password" placeholder="password" required '
            'style="width:100%;padding:10px;margin-bottom:10px">'
            '<button type="submit" style="width:100%;padding:10px">Войти</button>'
            "</form>",
            status_code=401,
        )
    plans = db.query(Plan).order_by(Plan.id).all()
    site = get_site(db)
    return templates.TemplateResponse("admin.html", {"request": request, "plans": plans, "site": site})


@router.post("/login")
def admin_login(password: str = Form(...)):
    if not settings.admin_password or password != settings.admin_password:
        raise HTTPException(status_code=403, detail="bad password")
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie(ADMIN_COOKIE, password, httponly=True, secure=settings.cookie_secure, samesite="lax")
    return resp


def _clean_payment_url(value: str) -> str:
    link = (value or "").strip()
    if not link:
        return ""
    if not link.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Ссылка Робокассы должна начинаться с http:// или https://")
    return link[:2000]


@router.post("/plan/{plan_id}")
def admin_update_plan(
    plan_id: int,
    name: str = Form(...),
    price: int = Form(...),
    days: int = Form(...),
    traffic_gb: int = Form(...),
    payment_url: str = Form(""),
    is_active: bool = Form(False),
    admin_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    _check(admin_token)
    plan = db.get(Plan, plan_id)
    if not plan:
        raise HTTPException(404, "plan not found")
    plan.name = name
    plan.price = price
    plan.days = days
    plan.traffic_gb = traffic_gb
    plan.payment_url = _clean_payment_url(payment_url)
    plan.is_active = is_active
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@router.post("/plan/{plan_id}/delete")
def admin_delete_plan(
    plan_id: int,
    admin_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    _check(admin_token)
    plan = db.get(Plan, plan_id)
    if plan:
        db.delete(plan)
        db.commit()
    return RedirectResponse("/admin", status_code=303)


@router.post("/site")
def admin_update_site(
    legal_name: str = Form(""),
    recipient_name: str = Form(""),
    inn: str = Form(""),
    privacy_policy: str = Form(""),
    public_offer: str = Form(""),
    admin_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    _check(admin_token)
    site = get_site(db)
    site.legal_name = (legal_name or "LotAi").strip()[:120] or "LotAi"
    site.recipient_name = recipient_name.strip()[:200]
    site.inn = "".join(ch for ch in inn if ch.isdigit())[:12]
    site.privacy_policy = privacy_policy
    site.public_offer = public_offer
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@router.post("/plan/create")
def admin_create_plan(
    name: str = Form(...),
    price: int = Form(...),
    days: int = Form(...),
    traffic_gb: int = Form(...),
    payment_url: str = Form(""),
    admin_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    _check(admin_token)
    db.add(Plan(
        name=name,
        price=price,
        days=days,
        traffic_gb=traffic_gb,
        payment_url=_clean_payment_url(payment_url),
        is_active=True,
    ))
    db.commit()
    return RedirectResponse("/admin", status_code=303)