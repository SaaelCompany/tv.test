"""Имена клиентов 3x-ui.

В панели имя клиента — это поле email. Пробелы и слэши там запрещены,
поэтому адрес пользователя остаётся адресом, а тариф пишется в comment:
его же панель подставляет в имя узла подписки через {{COMMENT}} / {{EMAIL}}.
"""

import re

_LEGACY_EMAIL = re.compile(r"^u\d+_o\d+$")
_LOCAL_SAFE = re.compile(r"[^A-Za-z0-9._+-]")
_DOMAIN_SAFE = re.compile(r"[^A-Za-z0-9.-]")
_SLOT = re.compile(r"\+(\d+)$")
_TITLE_SLOT = re.compile(r"#(\d+)\s*$")


def is_legacy_panel_email(email: str) -> bool:
    return bool(_LEGACY_EMAIL.match((email or "").strip()))


def panel_client_email(user_email: str, slot: int) -> str:
    """Уникальное имя клиента. slot=1 — почта пользователя, дальше local+N@domain."""
    raw = (user_email or "").strip().lower()
    local, _, domain = raw.partition("@")
    local = _LOCAL_SAFE.sub("", local) or "user"
    domain = _DOMAIN_SAFE.sub("", domain).strip(".") or "lumina.local"
    slot = max(int(slot or 1), 1)
    if slot == 1:
        return f"{local}@{domain}"
    return f"{local}+{slot}@{domain}"


def next_panel_email(user_email: str, taken: set[str]) -> tuple[str, int]:
    taken_norm = {item.strip().lower() for item in taken if item}
    for slot in range(1, 10001):
        candidate = panel_client_email(user_email, slot)
        if candidate not in taken_norm:
            return candidate, slot
    raise RuntimeError("не удалось подобрать свободное имя клиента")


def slot_from_email(email: str) -> int:
    local = (email or "").split("@", 1)[0]
    match = _SLOT.search(local)
    if not match:
        return 1
    return max(int(match.group(1)), 1)


def panel_comment(plan_name: str) -> str:
    name = (plan_name or "Lumina").strip()
    name = re.sub(r"[\x00-\x1f\x7f/\\]", "", name)
    return name[:120] or "Lumina"


def subscription_title(plan_name: str, slot: int) -> str:
    base = (plan_name or "Подписка").strip() or "Подписка"
    if slot <= 1:
        return base
    return f"{base} #{slot}"


def retitle(current_name: str, plan_name: str) -> str:
    match = _TITLE_SLOT.search(current_name or "")
    slot = int(match.group(1)) if match else 1
    return subscription_title(plan_name, slot)
