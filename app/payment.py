import hashlib
from decimal import Decimal
from urllib.parse import urlencode

from .config import settings
from .models import Order


def _hash(data: str) -> str:
    algo = settings.robokassa_hash_type.lower()
    if algo == "sha256":
        return hashlib.sha256(data.encode()).hexdigest()
    if algo == "sha512":
        return hashlib.sha512(data.encode()).hexdigest()
    return hashlib.md5(data.encode()).hexdigest()


def create_payment_url(order: Order) -> str:
    inv_id = str(order.id)
    order.inv_id = inv_id          # ← ВСЕГДА, до ветвления на mock/real

    if settings.robokassa_mock:
        return f"{settings.base_url.rstrip('/')}/payment/mock/{inv_id}"

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