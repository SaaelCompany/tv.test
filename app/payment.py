import hashlib
from decimal import Decimal
from urllib.parse import urlencode, urlparse

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


def create_payment_url(order: Order) -> str:
    inv_id = str(order.id)
    order.inv_id = inv_id          # ← ВСЕГДА, до ветвления на mock/real

    link = ""
    plan = getattr(order, "plan", None)
    if plan is not None:
        link = (getattr(plan, "payment_url", None) or "").strip()
    if link:
        return _same_host_path(link)

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
    }
    if settings.robokassa_test_mode:
        params["IsTest"] = "1"

    return "https://auth.robokassa.ru/Merchant/Index.aspx?" + urlencode(params)