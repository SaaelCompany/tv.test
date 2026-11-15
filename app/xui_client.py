import logging
import secrets
import threading
import uuid
from datetime import datetime, timedelta
from urllib.parse import quote

import httpx

from .config import settings

logger = logging.getLogger(__name__)

GB = 1024 ** 3
UNLIMITED_EXPIRY = datetime(2099, 1, 1)

# Поля, которые панель хранит на клиенте. Обновление заменяет строку целиком,
# поэтому при продлении отправляем их обратно, а не только срок и трафик.
_CLIENT_KEYS = (
    "email",
    "id",
    "password",
    "auth",
    "flow",
    "security",
    "totalGB",
    "expiryTime",
    "limitIp",
    "limitHwid",
    "tgId",
    "subId",
    "comment",
    "enable",
    "reset",
    "secret",
    "privateKey",
    "publicKey",
    "preSharedKey",
    "allowedIPs",
    "keepAlive",
    "adTag",
    "created_at",
    "updated_at",
)


class XUIError(RuntimeError):
    pass


def bytes_to_gb(total_bytes: int) -> int:
    if total_bytes <= 0:
        return 0
    return int(total_bytes) // GB


def gb_to_bytes(traffic_gb: int) -> int:
    if traffic_gb <= 0:
        return 0
    return int(traffic_gb) * GB


def expiry_to_ms(value: datetime | None) -> int:
    if value is None or value >= UNLIMITED_EXPIRY:
        return 0
    return int(value.timestamp() * 1000)


def extend_base(panel_expiry: datetime | None, local_expiry: datetime | None, now: datetime | None = None) -> datetime:
    """Продление идёт от более поздней живой даты. Бессрочная и уже истёкшая считаются от сейчас."""
    now = now or datetime.utcnow()
    base = panel_expiry or local_expiry or now
    if base >= UNLIMITED_EXPIRY or base < now:
        return now
    return base


def ms_to_expiry(expiry_ms: int) -> datetime | None:
    if not expiry_ms or int(expiry_ms) <= 0:
        return None
    return datetime.utcfromtimestamp(int(expiry_ms) / 1000)


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def snapshot_from_panel(get_obj: dict | None, traffic_obj: dict | None = None) -> dict | None:
    """Сводит ответ панели к одному снимку подписки."""
    if not get_obj:
        return None
    client = _as_dict(get_obj.get("client")) or get_obj
    traffic = _as_dict(traffic_obj) or _as_dict(get_obj.get("traffic"))
    if not client and not traffic:
        return None

    email = (client.get("email") or traffic.get("email") or "").strip()
    expiry_ms = int(client.get("expiryTime") or traffic.get("expiryTime") or 0)
    total_bytes = int(client.get("totalGB") or traffic.get("total") or 0)
    up = int(traffic.get("up") or 0)
    down = int(traffic.get("down") or 0)
    if "enable" in client:
        enable = bool(client.get("enable"))
    elif "enable" in traffic:
        enable = bool(traffic.get("enable"))
    else:
        enable = True

    raw_uuid = client.get("uuid") if isinstance(client.get("uuid"), str) else ""
    raw_id = client.get("id")
    if not raw_uuid and isinstance(raw_id, str) and "-" in raw_id:
        raw_uuid = raw_id

    return {
        "email": email,
        "sub_id": client.get("subId") or client.get("sub_id") or "",
        "comment": client.get("comment") or "",
        "client_uuid": raw_uuid or "",
        "expiry_time": ms_to_expiry(expiry_ms),
        "total_bytes": max(total_bytes, 0),
        "total_gb": bytes_to_gb(total_bytes),
        "upload_bytes": max(up, 0),
        "download_bytes": max(down, 0),
        "enable": enable,
        "raw_client": client,
    }


def build_update_payload(
    client: dict,
    *,
    expiry_ms: int | None = None,
    total_bytes: int | None = None,
    enable: bool | None = None,
    comment: str | None = None,
    new_email: str | None = None,
) -> dict:
    """Собирает тело update так, чтобы панель не затёрла uuid, subId и flow."""
    payload = {}
    for key in _CLIENT_KEYS:
        if key in client and client[key] is not None:
            payload[key] = client[key]
    raw_id = client.get("id")
    if "id" not in payload and isinstance(client.get("uuid"), str):
        payload["id"] = client["uuid"]
    elif isinstance(raw_id, str) and "-" in raw_id:
        payload["id"] = raw_id
    if expiry_ms is not None:
        payload["expiryTime"] = int(expiry_ms)
    if total_bytes is not None:
        payload["totalGB"] = int(total_bytes)
    if enable is not None:
        payload["enable"] = bool(enable)
    if comment is not None:
        payload["comment"] = comment
    if new_email:
        payload["email"] = new_email
    if not payload.get("email"):
        payload["email"] = client.get("email") or ""
    return payload


def email_taken(message: str) -> bool:
    text = (message or "").lower()
    return "already" in text or "duplicate" in text or "in use" in text or "занят" in text


def _json_or_empty(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def panel_missing(status: int, data: dict | None) -> bool:
    if status == 404:
        return True
    msg = str((data or {}).get("msg") or "").lower()
    return "not found" in msg or "не найден" in msg or "no such" in msg


class XUIClient:
    """Клиент REST API 3x-ui. В mock-режиме держит клиентов в памяти."""

    def __init__(self):
        self.base = (settings.xui_url or "").rstrip("/")
        self.client = httpx.Client(
            base_url=self.base or "http://127.0.0.1",
            timeout=30,
            verify=False,
            follow_redirects=True,
        )
        self._logged_in = False
        self._lock = threading.Lock()
        self._mock_clients: dict[str, dict] = {}

    def reset_mock(self) -> None:
        with self._lock:
            self._mock_clients.clear()

    # ─────────────────────── Аутентификация ───────────────────────
    def login(self) -> None:
        if settings.xui_mock_mode:
            return
        logger.info("3x-ui: login with username/password to %s", self.base)
        r = self.client.post(
            "/login",
            data={"username": settings.xui_username, "password": settings.xui_password},
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            raise XUIError(f"3x-ui login failed: {data}")
        self._logged_in = True
        logger.info("3x-ui: login OK")

    def _ensure_login(self) -> None:
        if settings.xui_mock_mode or self._logged_in:
            return
        if settings.xui_api_token:
            self.client.headers["Authorization"] = f"Bearer {settings.xui_api_token}"
            self._logged_in = True
            logger.info("3x-ui: using API token")
        else:
            self.login()

    def _path(self, suffix: str) -> str:
        # Абсолютный URL, чтобы «+» и «@» в имени клиента не разбирались как userinfo.
        return f"{self.base}{suffix}"

    def _client_path(self, action: str, email: str) -> str:
        return self._path(f"/panel/api/clients/{action}/{quote(email, safe='')}")

    # ─────────────────────── Информация ───────────────────────
    def list_inbounds(self) -> list[dict]:
        self._ensure_login()
        r = self.client.get("/panel/api/inbounds/list")
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            raise XUIError(f"list inbounds failed: {data}")
        return data.get("obj") or []

    def get_inbound(self, inbound_id: int) -> dict:
        self._ensure_login()
        r = self.client.get(f"/panel/api/inbounds/get/{inbound_id}")
        r.raise_for_status()
        data = r.json()
        if not data.get("success") or not data.get("obj"):
            raise XUIError(f"get inbound {inbound_id} failed: {data}")
        return data["obj"]

    def get_client(self, email: str) -> dict | None:
        if settings.xui_mock_mode:
            with self._lock:
                raw = self._mock_clients.get(email)
                return snapshot_from_panel(raw, raw.get("traffic") if raw else None) if raw else None
        self._ensure_login()
        r = self.client.get(self._client_path("get", email))
        data = _json_or_empty(r)
        if panel_missing(r.status_code, data):
            return None
        r.raise_for_status()
        if not data.get("success") or not data.get("obj"):
            raise XUIError(f"get client {email} failed: {data}")
        traffic = self._fetch_traffic(email)
        return snapshot_from_panel(data["obj"], traffic)

    def get_client_by_sub_id(self, sub_id: str) -> dict | None:
        if not sub_id:
            return None
        if settings.xui_mock_mode:
            with self._lock:
                for raw in self._mock_clients.values():
                    client = raw.get("client") or {}
                    if client.get("subId") == sub_id:
                        return snapshot_from_panel(raw, raw.get("traffic"))
            return None
        self._ensure_login()
        r = self.client.get(self._path(f"/panel/api/clients/get/subId/{quote(sub_id, safe='')}"))
        data = _json_or_empty(r)
        if panel_missing(r.status_code, data):
            return None
        r.raise_for_status()
        obj = data.get("obj")
        if not data.get("success") or not obj:
            raise XUIError(f"get client by subId failed: {data}")
        first = obj[0] if isinstance(obj, list) else obj
        email = (_as_dict(first.get("client")) or first).get("email") or ""
        traffic = self._fetch_traffic(email) if email else None
        return snapshot_from_panel(first, traffic)

    def _fetch_traffic(self, email: str) -> dict | None:
        try:
            r = self.client.get(self._client_path("traffic", email))
            if r.status_code != 200:
                return None
            data = r.json()
            if not data.get("success"):
                return None
            obj = data.get("obj")
            return obj if isinstance(obj, dict) else None
        except Exception:
            logger.warning("3x-ui: не удалось прочитать трафик %s", email, exc_info=True)
            return None

    # ─────────────────────── Создание клиента ───────────────────────
    def add_client(
        self,
        email: str,
        days: int,
        traffic_gb: int,
        comment: str = "",
        sub_id: str | None = None,
        client_uuid: str | None = None,
    ) -> dict:
        if settings.xui_mock_mode:
            return self._mock_add(email, days, traffic_gb, comment, sub_id, client_uuid)

        inbound = self.get_inbound(settings.xui_inbound_id)
        flow = self._detect_flow(inbound)

        client_uuid = client_uuid or str(uuid.uuid4())
        sub_id = sub_id or secrets.token_hex(8)
        expiry = datetime.utcnow() + timedelta(days=days)
        total_bytes = gb_to_bytes(traffic_gb)

        client_payload = {
            "email": email,
            "id": client_uuid,
            "subId": sub_id,
            "totalGB": total_bytes,
            "expiryTime": expiry_to_ms(expiry),
            "enable": True,
            "limitIp": 0,
            "tgId": 0,
            "comment": comment or "",
            "reset": 0,
        }
        if flow:
            client_payload["flow"] = flow

        body = {
            "client": client_payload,
            "inboundIds": [settings.xui_inbound_id],
        }

        r = self.client.post("/panel/api/clients/add", json=body)
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            message = str(data.get("msg") or data)
            if email_taken(message):
                raise XUIError(f"email already in use: {email}")
            raise XUIError(f"addClient failed: {data}")
        logger.info("3x-ui: client created email=%s comment=%s", email, comment)

        return {
            "uuid": client_uuid,
            "email": email,
            "sub_id": sub_id,
            "comment": comment or "",
            "expiry_time": expiry,
            "total_gb": traffic_gb,
            "total_bytes": total_bytes,
            "sub_url": self._make_sub_url(sub_id),
        }

    # ─────────────────────── Продление и правка имени ───────────────────────
    def extend_client(
        self,
        email: str,
        days: int,
        traffic_gb: int,
        current_expiry: datetime,
        current_gb: int,
        comment: str | None = None,
        sub_id: str | None = None,
        client_uuid: str | None = None,
    ) -> dict:
        if settings.xui_mock_mode:
            return self._mock_extend(email, days, traffic_gb, current_expiry, current_gb, comment)

        self._ensure_login()
        current = self.get_client(email)
        if current is None and sub_id:
            current = self.get_client_by_sub_id(sub_id)
            if current and current.get("email"):
                email = current["email"]

        if current is None:
            logger.warning("3x-ui: client %s not found, recreating", email)
            created = self.add_client(
                email=email,
                days=days,
                traffic_gb=traffic_gb,
                comment=comment or "",
                sub_id=sub_id,
                client_uuid=client_uuid,
            )
            return created

        now = datetime.utcnow()
        new_expiry = extend_base(current["expiry_time"], current_expiry, now) + timedelta(days=days)
        old_total = int(current["total_bytes"] or 0)
        add_bytes = gb_to_bytes(traffic_gb)
        new_total = 0 if traffic_gb <= 0 else (old_total + add_bytes if old_total > 0 else add_bytes)

        updated = self.update_client(
            email,
            expiry_ms=expiry_to_ms(new_expiry),
            total_bytes=new_total,
            enable=True,
            comment=comment,
        )
        sub = updated.get("sub_id") or current.get("sub_id") or ""
        return {
            "email": updated.get("email") or email,
            "sub_id": sub,
            "comment": updated.get("comment") or comment or "",
            "uuid": updated.get("client_uuid") or current.get("client_uuid") or "",
            "expiry_time": updated["expiry_time"] or new_expiry,
            "total_gb": bytes_to_gb(new_total),
            "total_bytes": new_total,
            "sub_url": self._make_sub_url(sub) if sub else None,
        }

    def rename_client(self, email: str, new_email: str, comment: str | None = None) -> dict:
        if settings.xui_mock_mode:
            with self._lock:
                raw = self._mock_clients.pop(email, None)
                if raw is None:
                    raise XUIError(f"client {email} not found")
                raw["client"]["email"] = new_email
                if comment is not None:
                    raw["client"]["comment"] = comment
                self._mock_clients[new_email] = raw
                return snapshot_from_panel(raw, raw.get("traffic"))
        return self.update_client(email, new_email=new_email, comment=comment)

    def update_client(
        self,
        email: str,
        *,
        expiry_ms: int | None = None,
        total_bytes: int | None = None,
        enable: bool | None = None,
        comment: str | None = None,
        new_email: str | None = None,
    ) -> dict:
        if settings.xui_mock_mode:
            return self._mock_update(
                email,
                expiry_ms=expiry_ms,
                total_bytes=total_bytes,
                enable=enable,
                comment=comment,
                new_email=new_email,
            )
        self._ensure_login()
        r = self.client.get(self._client_path("get", email))
        if r.status_code == 404:
            raise XUIError(f"client {email} not found")
        r.raise_for_status()
        data = r.json()
        obj = data.get("obj") or {}
        client = _as_dict(obj.get("client")) or _as_dict(obj)
        if not client:
            raise XUIError(f"client {email} not found in response")

        payload = build_update_payload(
            client,
            expiry_ms=expiry_ms,
            total_bytes=total_bytes,
            enable=enable,
            comment=comment,
            new_email=new_email,
        )
        r = self.client.post(self._client_path("update", email), json=payload)
        r.raise_for_status()
        result = r.json()
        if not result.get("success"):
            raise XUIError(f"updateClient failed: {result}")
        logger.info("3x-ui: client updated email=%s", new_email or email)

        fresh = self.get_client(new_email or email)
        if fresh:
            return fresh
        return snapshot_from_panel({"client": payload}) or {}

    # ─────────────────────── Helpers ───────────────────────
    def _detect_flow(self, inbound: dict) -> str:
        """flow=xtls-rprx-vision нужен только для VLESS+Reality."""
        protocol = inbound.get("protocol", "")
        stream = inbound.get("streamSettings", {})
        if isinstance(stream, str):
            try:
                import json
                stream = json.loads(stream)
            except Exception:
                stream = {}
        security = stream.get("security", "")
        return "xtls-rprx-vision" if protocol == "vless" and security == "reality" else ""

    def _make_sub_url(self, sub_id: str) -> str:
        if settings.xui_sub_base_url:
            return f"{settings.xui_sub_base_url.rstrip('/')}/sub/{sub_id}"
        return f"[MOCK] https://access.lotai.example/sub/{sub_id}"

    def _mock_update(
        self,
        email: str,
        *,
        expiry_ms: int | None = None,
        total_bytes: int | None = None,
        enable: bool | None = None,
        comment: str | None = None,
        new_email: str | None = None,
    ) -> dict:
        with self._lock:
            raw = self._mock_clients.get(email)
            if raw is None:
                raise XUIError(f"client {email} not found")
            client = raw["client"]
            traffic = raw.setdefault("traffic", {})
            if expiry_ms is not None:
                client["expiryTime"] = int(expiry_ms)
                traffic["expiryTime"] = int(expiry_ms)
            if total_bytes is not None:
                client["totalGB"] = int(total_bytes)
                traffic["total"] = int(total_bytes)
            if enable is not None:
                client["enable"] = bool(enable)
                traffic["enable"] = bool(enable)
            if comment is not None:
                client["comment"] = comment
            target = new_email or email
            if target != email:
                if target in self._mock_clients:
                    raise XUIError(f"email already in use: {target}")
                self._mock_clients.pop(email, None)
                client["email"] = target
                traffic["email"] = target
                self._mock_clients[target] = raw
            return snapshot_from_panel(raw, traffic) or {}

    def _mock_record(
        self,
        email: str,
        days: int,
        traffic_gb: int,
        comment: str,
        sub_id: str | None,
        client_uuid: str | None,
        expiry: datetime | None = None,
    ) -> dict:
        sub_id = sub_id or secrets.token_hex(8)
        client_uuid = client_uuid or str(uuid.uuid4())
        expiry = expiry or (datetime.utcnow() + timedelta(days=days))
        total_bytes = gb_to_bytes(traffic_gb)
        return {
            "client": {
                "email": email,
                "id": client_uuid,
                "subId": sub_id,
                "comment": comment or "",
                "totalGB": total_bytes,
                "expiryTime": expiry_to_ms(expiry),
                "enable": True,
                "flow": "",
            },
            "traffic": {
                "email": email,
                "up": 0,
                "down": 0,
                "total": total_bytes,
                "enable": True,
                "expiryTime": expiry_to_ms(expiry),
            },
        }

    def _public_from_mock(self, email: str, record: dict) -> dict:
        client = record["client"]
        expiry = ms_to_expiry(client["expiryTime"]) or UNLIMITED_EXPIRY
        return {
            "uuid": client["id"],
            "email": email,
            "sub_id": client["subId"],
            "comment": client.get("comment") or "",
            "expiry_time": expiry,
            "total_gb": bytes_to_gb(client["totalGB"]),
            "total_bytes": client["totalGB"],
            "sub_url": self._make_sub_url(client["subId"]),
        }

    def _mock_add(
        self,
        email: str,
        days: int,
        traffic_gb: int,
        comment: str = "",
        sub_id: str | None = None,
        client_uuid: str | None = None,
    ) -> dict:
        with self._lock:
            if email in self._mock_clients:
                raise XUIError(f"email already in use: {email}")
            record = self._mock_record(email, days, traffic_gb, comment, sub_id, client_uuid)
            self._mock_clients[email] = record
            return self._public_from_mock(email, record)

    def _mock_extend(
        self,
        email: str,
        days: int,
        traffic_gb: int,
        current_expiry: datetime,
        current_gb: int,
        comment: str | None,
    ) -> dict:
        with self._lock:
            record = self._mock_clients.get(email)
            if record is None:
                record = self._mock_record(email, days, traffic_gb, comment or "", None, None)
                self._mock_clients[email] = record
                return self._public_from_mock(email, record)

            client = record["client"]
            now = datetime.utcnow()
            new_expiry = extend_base(
                ms_to_expiry(client.get("expiryTime") or 0),
                current_expiry,
                now,
            ) + timedelta(days=days)
            old_total = int(client.get("totalGB") or 0)
            add_bytes = gb_to_bytes(traffic_gb)
            new_total = 0 if traffic_gb <= 0 else (old_total + add_bytes if old_total > 0 else add_bytes)
            client["expiryTime"] = expiry_to_ms(new_expiry)
            client["totalGB"] = new_total
            client["enable"] = True
            if comment is not None:
                client["comment"] = comment
            traffic = record.setdefault("traffic", {})
            traffic["total"] = new_total
            traffic["expiryTime"] = client["expiryTime"]
            traffic["enable"] = True
            return self._public_from_mock(email, record)


xui = XUIClient()
