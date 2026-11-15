"""Подтягивает срок, квоту, трафик и имя клиента из 3x-ui в локальную подписку."""

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from .config import settings
from .models import Order, Subscription
from .naming import is_legacy_panel_email, next_panel_email, panel_comment, subscription_title
from .xui_client import UNLIMITED_EXPIRY, XUIError, email_taken, xui

logger = logging.getLogger(__name__)


def _plan_name_for(db: Session, sub: Subscription) -> str:
    if sub.name:
        return sub.name.split(" #", 1)[0].strip() or sub.name
    import re
    match = re.match(r"^u\d+_o(\d+)$", sub.email or "")
    if match:
        order = db.get(Order, int(match.group(1)))
        if order and order.plan:
            return order.plan.name
    return "Подписка"


def apply_snapshot(sub: Subscription, snap: dict, sub_url: str | None = None) -> None:
    expiry = snap.get("expiry_time") or UNLIMITED_EXPIRY
    sub.expiry_time = expiry
    sub.traffic_gb = int(snap.get("total_gb") or 0)
    sub.total_bytes = int(snap.get("total_bytes") or 0)
    sub.upload_bytes = int(snap.get("upload_bytes") or 0)
    sub.download_bytes = int(snap.get("download_bytes") or 0)
    sub.panel_enable = bool(snap.get("enable", True))

    now = datetime.utcnow()
    expired = snap.get("expiry_time") is not None and snap["expiry_time"] <= now
    depleted = sub.total_bytes > 0 and (sub.upload_bytes + sub.download_bytes) >= sub.total_bytes
    sub.active = sub.panel_enable and not expired and not depleted

    if snap.get("sub_id"):
        sub.sub_id = snap["sub_id"]
    if snap.get("comment"):
        sub.comment = snap["comment"]
    if snap.get("client_uuid"):
        sub.client_uuid = snap["client_uuid"]
    if snap.get("email") and snap["email"] != sub.email:
        sub.email = snap["email"]
    if sub_url:
        sub.sub_url = sub_url
    elif sub.sub_id and not sub.sub_url:
        sub.sub_url = xui._make_sub_url(sub.sub_id)
    sub.last_sync_at = now
    sub.sync_error = ""


def _ensure_readable_name(db: Session, sub: Subscription) -> None:
    """Старые клиенты u{user}_o{order} переименовываются в почту пользователя."""
    if not is_legacy_panel_email(sub.email):
        if not sub.comment and sub.name:
            try:
                xui.update_client(sub.email, comment=panel_comment(sub.name.split(" #", 1)[0]))
                sub.comment = panel_comment(sub.name.split(" #", 1)[0])
            except XUIError:
                logger.warning("3x-ui: не удалось записать комментарий %s", sub.email, exc_info=True)
        return

    user = sub.user
    if user is None:
        return
    plan_name = _plan_name_for(db, sub)
    taken = {
        other.email
        for other in db.query(Subscription).filter(Subscription.user_id == user.id).all()
        if other.id != sub.id
    }
    comment = panel_comment(plan_name)
    snap = None
    new_email = sub.email
    slot = 1
    for _ in range(6):
        new_email, slot = next_panel_email(user.email, taken)
        try:
            snap = xui.rename_client(sub.email, new_email, comment=comment)
            break
        except XUIError as exc:
            if email_taken(str(exc)):
                taken.add(new_email)
                continue
            logger.warning("3x-ui: не удалось переименовать %s", sub.email, exc_info=True)
            return
    else:
        return
    sub.email = new_email
    sub.comment = comment
    sub.name = subscription_title(plan_name, slot)
    if snap:
        apply_snapshot(sub, snap, xui._make_sub_url(snap["sub_id"]) if snap.get("sub_id") else None)


def sync_subscription(db: Session, sub: Subscription) -> Subscription:
    if settings.xui_mock_mode and sub.email not in xui._mock_clients and not is_legacy_panel_email(sub.email):
        sub.last_sync_at = datetime.utcnow()
        return sub

    try:
        _ensure_readable_name(db, sub)
        snap = xui.get_client(sub.email)
        if snap is None and sub.sub_id:
            snap = xui.get_client_by_sub_id(sub.sub_id)
        if snap is None:
            sub.sync_error = "Пространство не найдено в облаке"
            sub.active = False
            sub.last_sync_at = datetime.utcnow()
            return sub
        url = xui._make_sub_url(snap["sub_id"]) if snap.get("sub_id") else None
        apply_snapshot(sub, snap, url)
        if not sub.name:
            sub.name = snap.get("comment") or "Подписка"
    except Exception as exc:
        logger.exception("sync failed email=%s", sub.email)
        sub.sync_error = "Облако временно недоступно"
        sub.last_sync_at = datetime.utcnow()
        # не затираем рабочие данные, если панель временно не отвечает
        _ = exc
    return sub


def sync_user(db: Session, user_id: int, *, force: bool = False, max_age_sec: int = 30) -> None:
    subs = db.query(Subscription).filter(Subscription.user_id == user_id).all()
    now = datetime.utcnow()
    for sub in subs:
        if (
            not force
            and sub.last_sync_at
            and (now - sub.last_sync_at).total_seconds() < max_age_sec
            and not sub.sync_error
        ):
            continue
        sync_subscription(db, sub)
    db.commit()


def sync_all(db: Session) -> int:
    subs = db.query(Subscription).all()
    for sub in subs:
        sync_subscription(db, sub)
    db.commit()
    return len(subs)
