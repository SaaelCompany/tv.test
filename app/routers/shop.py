import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Order, Plan, Subscription, User
from ..payment import create_payment_url

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def get_or_create_user(db: Session, email: str) -> User:
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(email=email, token=secrets.token_urlsafe(32))
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


@router.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    plans = db.query(Plan).filter(Plan.is_active.is_(True)).all()
    return templates.TemplateResponse("index.html", {"request": request, "plans": plans})


@router.post("/buy")
def buy(
    request: Request,
    email: str = Form(...),
    plan_id: int = Form(...),
    mode: str = Form("new"),
    subscription_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    plan = db.get(Plan, plan_id)
    if not plan or not plan.is_active:
        return RedirectResponse("/", status_code=303)

    email = email.strip().lower()
    user = get_or_create_user(db, email)

    kind = "new"
    target_id = None
    if (mode or "").strip().lower() == "renew" and subscription_id:
        try:
            target_id = int(subscription_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Некорректная подписка")
        sub = db.get(Subscription, target_id)
        if sub is None or sub.user_id != user.id:
            raise HTTPException(status_code=404, detail="Подписка не найдена")
        kind = "renew"

    order = Order(
        inv_id="",              # заполним после flush
        user_id=user.id,
        plan_id=plan.id,
        subscription_id=target_id,
        kind=kind,
        amount=plan.price,
        status="pending",
    )
    db.add(order)
    db.flush()                  # получаем order.id, БЕЗ commit
    url = create_payment_url(order)   # выставляет order.inv_id = str(order.id)
    db.commit()                 # сохраняем inv_id вместе с ордером

    response = RedirectResponse(url, status_code=303)
    response.set_cookie(
        "user_token", user.token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=86400 * 30,
    )
    return response