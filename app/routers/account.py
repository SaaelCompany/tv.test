from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from ..templating import render
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Order, Plan, Subscription, User
from ..payment import create_payment_url
from ..sync import sync_user

router = APIRouter()


def format_traffic(num_bytes: int) -> str:
    value = float(max(int(num_bytes or 0), 0))
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if value < 1024 or unit == "ТБ":
            if unit == "Б":
                return f"{int(value)} {unit}"
            text = f"{value:.1f}".rstrip("0").rstrip(".")
            return f"{text} {unit}"
        value /= 1024
    return f"{int(num_bytes or 0)} Б"


def subscription_status(sub: Subscription, now: datetime) -> tuple[str, str]:
    error = (sub.sync_error or "").lower()
    if "не найден" in error:
        return "missing", "Нет в облаке"
    if sub.panel_enable is False:
        return "disabled", "Отключена"
    if sub.expiry_time and sub.expiry_time.year < 2090 and sub.expiry_time <= now:
        return "expired", "Истекла"
    used = (sub.upload_bytes or 0) + (sub.download_bytes or 0)
    if sub.total_bytes and used >= sub.total_bytes:
        return "depleted", "Квота исчерпана"
    if not sub.active:
        return "inactive", "Неактивна"
    return "active", "Активна"


def traffic_text(sub: Subscription) -> str:
    used = (sub.upload_bytes or 0) + (sub.download_bytes or 0)
    used_label = format_traffic(used)
    if sub.total_bytes:
        return f"{used_label} из {format_traffic(sub.total_bytes)}"
    if sub.traffic_gb:
        return f"{used_label} из {sub.traffic_gb} ГБ"
    return f"{used_label} из безлимита"


def expiry_text(sub: Subscription) -> str:
    if not sub.expiry_time or sub.expiry_time.year >= 2090:
        return "Бессрочно"
    return sub.expiry_time.strftime("%d.%m.%Y %H:%M") + " UTC"


def present(sub: Subscription, now: datetime) -> dict:
    code, label = subscription_status(sub, now)
    return {
        "obj": sub,
        "status": code,
        "status_label": label,
        "traffic_text": traffic_text(sub),
        "expiry_text": expiry_text(sub),
    }


def _user(db: Session, token: Optional[str]) -> Optional[User]:
    if not token:
        return None
    return db.query(User).filter(User.token == token).first()


@router.get("/account", response_class=HTMLResponse)
def account(
    request: Request,
    user_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    user = _user(db, user_token)
    if not user:
        return RedirectResponse("/", status_code=303)

    try:
        sync_user(db, user.id)
    except Exception:
        db.rollback()

    subs = (
        db.query(Subscription)
        .filter(Subscription.user_id == user.id)
        .order_by(Subscription.created_at.asc())
        .all()
    )
    orders = (
        db.query(Order)
        .filter(Order.user_id == user.id)
        .order_by(Order.created_at.desc())
        .all()
    )
    plans = db.query(Plan).filter(Plan.is_active.is_(True)).order_by(Plan.price).all()
    for order in orders:
        order.pay_url = create_payment_url(order) if order.status == "pending" else ""
    now = datetime.utcnow()
    return render(
        request,
        "account.html",
        {
            "user": user,
            "subscriptions": [present(sub, now) for sub in subs],
            "orders": orders,
            "plans": plans,
        },
    )


@router.post("/account/sync")
def account_sync(
    user_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    user = _user(db, user_token)
    if not user:
        return RedirectResponse("/", status_code=303)
    sync_user(db, user.id, force=True)
    return RedirectResponse("/account", status_code=303)


@router.post("/logout")
def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("user_token")
    return response