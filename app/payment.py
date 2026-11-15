import hashlib
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy.orm import Session

from .config import settings
from .models import Order


def _hash(data: str) -> str:
    algo = settings.robokassa_hash_type.lower()
    if algo == "sha256":
        return hashlib.sha256(data.encode()).hexdigest()
    if algo == "sha512":
        return hashlib.sha512(data.encode()).hexdigest()
    return hashlib.md5(data.encode()).hexdigest()


def _same_host_path(url: str) -> str:
    """localhost в ссылке открывается на компьютере покупателя, а не на сервере."""
    parsed = urlparse(url)
    if parsed.hostname in {"localhost", "127.0.0.1"}:
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return path
    return url


def signature_for(out_sum: str, inv_id: str, password: str) -> str:
    return _hash(f"{out_sum}:{inv_id}:{password}").lower()


def same_payment_sum(order: Order, out_sum: str) -> bool:
    try:
        return Decimal(order.amount) == Decimal(str(out_sum).replace(",", ".").strip())
    except (InvalidOperation, TypeError, ValueError):
        return False


def _whole_rubles(out_sum: str) -> int | None:
    try:
        value = Decimal(str(out_sum).replace(",", ".").strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    if value != value.to_integral_value():
        return None
    return int(value)


def match_payment(db: Session, inv_id: str, out_sum: str) -> Order | None:
    """Найти заказ для уведомления Робокассы.

    Готовая ссылка шлёт один и тот же InvId каждому покупателю. Уже оплаченный
    заказ с этим номером не закрывает следующий платёж.
    """
    inv_id = str(inv_id)
    own = db.query(Order).filter(Order.inv_id == inv_id).first()
    static = bool(own and (getattr(own.plan, "payment_url", "") or "").strip())
    if own is not None and not static:
        return own

    linked = (
        db.query(Order)
        .filter(Order.provider_inv_id == inv_id, Order.status == "pending")
        .order_by(Order.created_at.asc(), Order.id.asc())
        .all()
    )
    for order in linked:
        if same_payment_sum(order, out_sum):
            return order

    amount = _whole_rubles(out_sum)
    if amount is not None:
        pending = (
            db.query(Order)
            .filter(Order.status == "pending", Order.amount == amount)
            .order_by(Order.created_at.asc(), Order.id.asc())
            .all()
        )
        for order in pending:
            if same_payment_sum(order, out_sum):
                order.provider_inv_id = inv_id
                return order

    paid = (
        db.query(Order)
        .filter(Order.provider_inv_id == inv_id, Order.status == "paid")
        .order_by(Order.id.desc())
        .all()
    )
    for order in paid:
        if same_payment_sum(order, out_sum):
            return order
    return None


def _with_return_urls(link: str) -> str:
    parsed = urlparse(link)
    if parsed.scheme not in {"http", "https"}:
        return link
    if "robokassa" not in (parsed.hostname or "").lower():
        return link
    base = (settings.base_url or "").rstrip("/")
    if not base.startswith(("http://", "https://")):
        return link
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    keys = {name.lower() for name, _ in pairs}
    if "successurl" not in keys:
        pairs.append(("SuccessUrl", f"{base}/payment/success"))
    if "failurl" not in keys:
        pairs.append(("FailUrl", f"{base}/payment/fail"))
    return urlunparse(parsed._replace(query=urlencode(pairs)))


def create_payment_url(order: Order) -> str:
    inv_id = str(order.id)
    order.inv_id = inv_id          # ← ВСЕГДА, до ветвления на mock/real

    link = ""
    plan = getattr(order, "plan", None)
    if plan is not None:
        link = (getattr(plan, "payment_url", None) or "").strip()
    if link:
        return _with_return_urls(_same_host_path(link))

    # Тестовая страница только если её явно включили и у тарифа нет ссылки.
    if settings.robokassa_mock:
        return f"/payment/mock/{inv_id}"

    if not settings.robokassa_login or not settings.robokassa_password1:
        return f"/payment/unavailable?order={inv_id}"

    out_sum = f"{Decimal(order.amount):.2f}"

    signature_base = f"{settings.robokassa_login}:{out_sum}:{inv_id}:{settings.robokassa_password1}"
    signature = _hash(signature_base)
    action = "продление" if getattr(order, "kind", "new") == "renew" else "подписка"

    params = {
        "MerchantLogin": settings.robokassa_login,
        "OutSum": out_sum,
        "InvId": inv_id,
        "Description": f"LotAi {action} {order.plan.name}",
        "SignatureValue": signature,
        "Culture": "ru",
        "SuccessUrl": f"{settings.base_url.rstrip('/')}/payment/success",
        "FailUrl": f"{settings.base_url.rstrip('/')}/payment/fail",
    }
    if settings.robokassa_test_mode:
        params["IsTest"] = "1"

    return "https://auth.robokassa.ru/Merchant/Index.aspx?" + urlencode(params)