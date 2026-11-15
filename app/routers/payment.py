import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Order, Subscription, User
from ..naming import next_panel_email, panel_comment, retitle, subscription_title
from ..payment import match_payment, same_payment_sum, signature_for
from ..sync import sync_subscription
from ..templating import render
from ..xui_client import XUIError, email_taken, gb_to_bytes, xui

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/unavailable", response_class=HTMLResponse)
def payment_unavailable(request: Request):
    return render(request, "payment_unavailable.html", {})


@router.get("/mock/{inv_id}", response_class=HTMLResponse)
def mock_payment_page(request: Request, inv_id: str, db: Session = Depends(get_db)):
    if not settings.robokassa_mock:
        return HTMLResponse("Not found", status_code=404)
    order = db.query(Order).filter(Order.inv_id == inv_id).first()
    if not order:
        return HTMLResponse("Заказ не найден", status_code=404)
    return render(request, "mock_payment.html", {"order": order})


@router.post("/mock/{inv_id}/confirm")
def mock_confirm(inv_id: str, db: Session = Depends(get_db)):
    if not settings.robokassa_mock:
        return RedirectResponse("/", status_code=303)
    order = db.query(Order).filter(Order.inv_id == inv_id).first()
    if not order or order.status != "pending":
        return RedirectResponse("/account", status_code=303)
    process_successful_payment(db, order)
    return RedirectResponse("/account", status_code=303)


def _robokassa_fields(source) -> tuple[str, str, str]:
    out_sum = str(source.get("OutSum", "") or "")
    inv_id = str(source.get("InvId", "") or "")
    signature = str(source.get("SignatureValue", "") or "").lower()
    return out_sum, inv_id, signature


def _paid_for_user(db: Session, user_id: int, inv_id: str, out_sum: str) -> Order | None:
    """Возврат браузера выдаёт ключ только заказу этого человека."""
    pending = (
        db.query(Order)
        .filter(Order.user_id == user_id, Order.status == "pending")
        .order_by(Order.id.desc())
        .all()
    )
    for order in pending:
        if same_payment_sum(order, out_sum):
            order.provider_inv_id = str(inv_id)
            return _apply_paid_notice_order(db, order, out_sum, inv_id)
    paid = (
        db.query(Order)
        .filter(Order.user_id == user_id, Order.status == "paid")
        .order_by(Order.id.desc())
        .all()
    )
    for order in paid:
        if same_payment_sum(order, out_sum):
            return order
    return None


def _paid_without_session(db: Session, inv_id: str, out_sum: str) -> Order | None:
    """Без cookie не выдаём второй ключ на уже использованный InvId готовой ссылки."""
    used = (
        db.query(Order)
        .filter(Order.provider_inv_id == str(inv_id), Order.status == "paid")
        .all()
    )
    if any(same_payment_sum(order, out_sum) for order in used):
        return None
    return _apply_paid_notice(db, out_sum, inv_id)


def _apply_paid_notice_order(db: Session, order: Order, out_sum: str, inv_id: str) -> Order | None:
    if order.status == "paid":
        return order
    if not same_payment_sum(order, out_sum):
        logger.error(
            "Robokassa: amount mismatch inv_id=%s order=%s got=%s",
            inv_id, order.amount, out_sum,
        )
        return None
    process_successful_payment(db, order)
    return order


def _apply_paid_notice(db: Session, out_sum: str, inv_id: str) -> Order | None:
    order = match_payment(db, inv_id, out_sum)
    if order is None:
        logger.warning("Robokassa: order not found inv_id=%s sum=%s", inv_id, out_sum)
        return None
    if order.status == "paid":
        return order
    if not same_payment_sum(order, out_sum):
        logger.error(
            "Robokassa: amount mismatch inv_id=%s order=%s got=%s",
            inv_id, order.amount, out_sum,
        )
        return None
    process_successful_payment(db, order)
    return order


@router.post("/result", response_class=PlainTextResponse)
async def result_url(request: Request, db: Session = Depends(get_db)):
    """Result URL от Robokassa. Всегда проверяем подпись паролем №2."""
    form = await request.form()
    out_sum, inv_id, signature = _robokassa_fields(form)

    if not (out_sum and inv_id and signature):
        logger.warning("Robokassa: missing params: %s", dict(form))
        return PlainTextResponse("ERROR", status_code=400)

    expected = signature_for(out_sum, inv_id, settings.robokassa_password2)
    if expected != signature:
        logger.warning(
            "Robokassa: bad signature inv_id=%s. Проверьте ROBOKASSA_PASSWORD2 и ROBOKASSA_HASH_TYPE",
            inv_id,
        )
        return PlainTextResponse("ERROR", status_code=403)

    order = _apply_paid_notice(db, out_sum, inv_id)
    if order is None:
        return PlainTextResponse("ERROR", status_code=404)
    return PlainTextResponse(f"OK{inv_id}")


@router.get("/success", response_class=HTMLResponse)
def payment_success(
    request: Request,
    user_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db),
):
    """Сюда Робокасса возвращает браузер. Ключ показывается на этой странице."""
    out_sum, inv_id, signature = _robokassa_fields(request.query_params)
    order = None
    user = db.query(User).filter(User.token == user_token).first() if user_token else None
    if out_sum and inv_id and signature and settings.robokassa_password1:
        expected = signature_for(out_sum, inv_id, settings.robokassa_password1)
        if expected == signature:
            if user is not None:
                order = _paid_for_user(db, user.id, inv_id, out_sum)
            else:
                order = _paid_without_session(db, inv_id, out_sum)
        else:
            logger.warning("Robokassa success: bad signature inv_id=%s", inv_id)
    if order is None and user is not None:
        order = (
            db.query(Order)
            .filter(Order.user_id == user.id)
            .order_by(Order.id.desc())
            .first()
        )

    sub = None
    if order is not None and order.status == "paid":
        if order.subscription_id:
            sub = db.get(Subscription, order.subscription_id)
        if sub is None:
            sub = (
                db.query(Subscription)
                .filter(Subscription.user_id == order.user_id)
                .order_by(Subscription.id.desc())
                .first()
            )

    waiting = order is not None and order.status == "pending"
    response = render(
        request,
        "payment_success.html",
        {"sub_url": sub.sub_url if sub else "", "waiting": waiting},
    )
    if order is not None and order.user is not None:
        response.set_cookie(
            "user_token",
            order.user.token,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
            max_age=86400 * 30,
        )
    return response


@router.get("/fail", response_class=HTMLResponse)
def payment_fail(request: Request):
    return render(request, "payment_fail.html", {})


def _apply_result(sub: Subscription, result: dict) -> None:
    sub.expiry_time = result["expiry_time"]
    sub.traffic_gb = result.get("total_gb") or 0
    sub.total_bytes = result.get("total_bytes") or gb_to_bytes(sub.traffic_gb)
    sub.panel_enable = True
    sub.active = True
    sub.sync_error = ""
    if result.get("comment"):
        sub.comment = result["comment"]
    if result.get("sub_url"):
        sub.sub_url = result["sub_url"]
    if result.get("sub_id"):
        sub.sub_id = result["sub_id"]
    if result.get("uuid"):
        sub.client_uuid = result["uuid"]
    if result.get("email"):
        sub.email = result["email"]


def _create_subscription(db: Session, order: Order) -> tuple[Subscription, dict]:
    plan = order.plan
    user = order.user
    taken = {row[0] for row in db.query(Subscription.email).all()}
    comment = panel_comment(plan.name)
    last_error: Exception | None = None
    for _ in range(8):
        email, slot = next_panel_email(user.email, taken)
        try:
            result = xui.add_client(
                email=email,
                days=plan.days,
                traffic_gb=plan.traffic_gb,
                comment=comment,
            )
        except XUIError as exc:
            last_error = exc
            if email_taken(str(exc)):
                taken.add(email)
                continue
            raise
        sub = Subscription(
            user_id=user.id,
            email=result["email"],
            name=subscription_title(plan.name, slot),
            comment=comment,
            sub_id=result["sub_id"],
            sub_url=result["sub_url"],
            client_uuid=result.get("uuid") or "",
            expiry_time=result["expiry_time"],
            traffic_gb=result.get("total_gb") or 0,
            total_bytes=result.get("total_bytes") or gb_to_bytes(plan.traffic_gb),
            panel_enable=True,
            active=True,
        )
        db.add(sub)
        db.flush()
        return sub, result
    raise last_error or XUIError("не удалось создать клиента в панели")


def _renew_subscription(order: Order, sub: Subscription) -> dict:
    plan = order.plan
    comment = panel_comment(plan.name)
    result = xui.extend_client(
        email=sub.email,
        days=plan.days,
        traffic_gb=plan.traffic_gb,
        current_expiry=sub.expiry_time,
        current_gb=sub.traffic_gb,
        comment=comment,
        sub_id=sub.sub_id,
        client_uuid=sub.client_uuid or None,
    )
    sub.name = retitle(sub.name or plan.name, plan.name)
    sub.comment = comment
    _apply_result(sub, result)
    return result


def process_successful_payment(db: Session, order: Order):
    if order.status == "paid":
        return

    order.status = "paid"
    order.paid_at = datetime.utcnow()
    db.flush()

    sub = None
    if order.kind == "renew" and order.subscription_id:
        sub = db.get(Subscription, order.subscription_id)
        if sub is None or sub.user_id != order.user_id:
            logger.error(
                "renew target missing order=%s subscription=%s",
                order.id,
                order.subscription_id,
            )
            sub = None

    if sub is not None:
        result = _renew_subscription(order, sub)
    else:
        sub, result = _create_subscription(db, order)

    order.subscription_id = sub.id
    sync_subscription(db, sub)
    db.commit()

    sub_url = result.get("sub_url") or sub.sub_url
    if settings.smtp_host and sub_url:
        try:
            from ..mailer import send_subscription_email
            send_subscription_email(order.user.email, sub_url)
        except Exception:
            logger.exception("Email send failed")