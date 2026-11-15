import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Order, Subscription
from ..naming import next_panel_email, panel_comment, retitle, subscription_title
from ..payment import _hash
from ..sync import sync_subscription
from ..templating import render
from ..xui_client import XUIError, email_taken, gb_to_bytes, xui

logger = logging.getLogger(__name__)
router = APIRouter()


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


@router.post("/result", response_class=PlainTextResponse)
async def result_url(request: Request, db: Session = Depends(get_db)):
    """Result URL от Robokassa. Всегда проверяем подпись."""
    form = await request.form()
    out_sum = form.get("OutSum", "")
    inv_id = form.get("InvId", "")
    signature = (form.get("SignatureValue", "") or "").lower()

    if not (out_sum and inv_id and signature):
        logger.warning("Robokassa: missing params: %s", dict(form))
        return PlainTextResponse("ERROR", status_code=400)

    expected = _hash(f"{out_sum}:{inv_id}:{settings.robokassa_password2}").lower()
    if expected != signature:
        logger.warning("Robokassa: bad signature inv_id=%s got=%s expected=%s",
                       inv_id, signature, expected)
        return PlainTextResponse("ERROR", status_code=403)

    order = db.query(Order).filter(Order.inv_id == str(inv_id)).first()
    if not order:
        logger.warning("Robokassa: order not found inv_id=%s", inv_id)
        return PlainTextResponse("ERROR", status_code=404)

    if order.status == "paid":
        return PlainTextResponse(f"OK{inv_id}")

    # Сверяем сумму (защита от подмены)
    from decimal import Decimal
    if Decimal(str(order.amount)) != Decimal(out_sum):
        logger.error("Robokassa: amount mismatch inv_id=%s order=%s got=%s",
                     inv_id, order.amount, out_sum)
        return PlainTextResponse("ERROR", status_code=400)

    process_successful_payment(db, order)
    return PlainTextResponse(f"OK{inv_id}")


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

    sync_subscription(db, sub)
    db.commit()

    sub_url = result.get("sub_url") or sub.sub_url
    if settings.smtp_host and sub_url:
        try:
            from ..mailer import send_subscription_email
            send_subscription_email(order.user.email, sub_url)
        except Exception:
            logger.exception("Email send failed")