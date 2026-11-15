from datetime import datetime, timedelta
from urllib.parse import quote, urlparse

import httpx

from app.database import SessionLocal, ensure_schema
from app.models import Order, Subscription, User
from app.naming import is_legacy_panel_email, next_panel_email, panel_comment, retitle
from app.sync import sync_subscription
from app.xui_client import build_update_payload, extend_base, snapshot_from_panel, xui


def _pay(client, email, plan_id, **extra):
    data = {"email": email, "plan_id": str(plan_id), "mode": "new"}
    data.update(extra)
    response = client.post("/buy", data=data, follow_redirects=False)
    assert response.status_code == 303, response.text
    path = urlparse(response.headers["location"]).path
    paid = client.post(path + "/confirm", follow_redirects=True)
    assert paid.status_code == 200
    return paid


def test_host_header_allows_ip_and_nip():
    from app.main import host_allowed

    assert host_allowed("179.254.168.161", ["localhost"])
    assert host_allowed("179.254.168.161.nip.io", ["localhost"])
    assert host_allowed("shop.example", ["shop.example"])
    assert not host_allowed("evil.example", ["localhost"])


def test_storefront_is_cloud_access(client):
    page = client.get("/")
    assert page.status_code == 200
    low = page.text.lower()
    assert "lotai" in low
    assert "политика конфиденциальности" in low
    assert "публичная оферта" in low
    assert "vpn" not in low
    assert "vless" not in low
    assert "облачн" in low


def test_admin_edits_footer_requisites(client):
    login = client.post("/admin/login", data={"password": "test-admin"}, follow_redirects=False)
    assert login.status_code == 303
    saved = client.post(
        "/admin/site",
        data={
            "legal_name": "LotAi",
            "recipient_name": "Заверюха А.Н.",
            "inn": "2124 08998175",
            "privacy_policy": "Мы храним почту и срок доступа.",
            "public_offer": "Оплата открывает ключ доступа.",
        },
        follow_redirects=True,
    )
    assert saved.status_code == 200
    home = client.get("/")
    assert "Заверюха А.Н." in home.text
    assert "ИНН: 212408998175" in home.text
    privacy = client.get("/privacy")
    assert privacy.status_code == 200
    assert "Мы храним почту и срок доступа." in privacy.text
    offer = client.get("/offer")
    assert "Оплата открывает ключ доступа." in offer.text
    assert "vpn" not in privacy.text.lower()


def test_names_and_snapshot_helpers():
    email, slot = next_panel_email("Anna@Example.com", set())
    assert email == "anna@example.com"
    assert slot == 1
    second, slot = next_panel_email("anna@example.com", {email})
    assert second == "anna+2@example.com"
    assert slot == 2
    assert is_legacy_panel_email("u3_o12")
    assert not is_legacy_panel_email("anna@example.com")
    assert panel_comment("Старт / тест") == "Старт  тест"
    assert retitle("Старт #2", "Годовой") == "Годовой #2"
    assert retitle("Старт", "Годовой") == "Годовой"

    snap = snapshot_from_panel(
        {
            "client": {
                "email": "anna@example.com",
                "id": "11111111-1111-1111-1111-111111111111",
                "subId": "abc",
                "comment": "Старт",
                "totalGB": 50 * 1024 ** 3,
                "expiryTime": 1_700_000_000_000,
                "enable": True,
                "flow": "xtls-rprx-vision",
            }
        },
        {"up": 1024, "down": 2048, "total": 50 * 1024 ** 3},
    )
    assert snap["email"] == "anna@example.com"
    assert snap["comment"] == "Старт"
    assert snap["upload_bytes"] == 1024
    assert snap["total_gb"] == 50
    payload = build_update_payload(snap["raw_client"], expiry_ms=5, comment="Премиум")
    assert payload["id"] == "11111111-1111-1111-1111-111111111111"
    assert payload["subId"] == "abc"
    assert payload["flow"] == "xtls-rprx-vision"
    assert payload["expiryTime"] == 5
    assert payload["comment"] == "Премиум"

    now = datetime(2026, 1, 1)
    assert extend_base(now + timedelta(days=10), now, now) == now + timedelta(days=10)
    assert extend_base(now - timedelta(days=1), now, now) == now


def test_panel_email_is_not_parsed_as_userinfo():
    email = "anna+2@example.com"
    url = httpx.URL("http://127.0.0.1:2053/panel/api/clients/get/" + quote(email, safe=""))
    assert url.host == "127.0.0.1"
    assert quote(email, safe="") in str(url)


def test_second_purchase_is_a_new_subscription_and_renew_extends_one(client):
    email = "anna@example.com"
    _pay(client, email, 1)
    _pay(client, email, 1)

    page = client.get("/account")
    assert page.status_code == 200
    assert page.text.count("anna@example.com") >= 1
    assert "anna+2@example.com" in page.text
    assert "Старт #2" in page.text
    assert page.text.count("Ключ доступа") == 2

    db = SessionLocal()
    try:
        subs = db.query(Subscription).order_by(Subscription.id).all()
        assert len(subs) == 2
        assert subs[0].email == "anna@example.com"
        assert subs[0].comment == "Старт"
        assert subs[1].comment == "Старт"
        first_expiry = subs[0].expiry_time
        second_expiry = subs[1].expiry_time
        first_id = subs[0].id
    finally:
        db.close()

    _pay(client, email, 4, mode="renew", subscription_id=str(first_id))

    db = SessionLocal()
    try:
        subs = db.query(Subscription).order_by(Subscription.id).all()
        assert len(subs) == 2
        assert subs[0].name == "Годовой"
        assert subs[0].comment == "Годовой"
        assert subs[0].expiry_time > first_expiry + timedelta(days=360)
        assert abs((subs[1].expiry_time - second_expiry).total_seconds()) < 2
        orders = db.query(Order).filter(Order.kind == "renew").all()
        assert len(orders) == 1
        assert orders[0].subscription_id == first_id
    finally:
        db.close()

    panel = xui.get_client("anna@example.com")
    assert panel["comment"] == "Годовой"
    assert panel["sub_id"]
    assert xui.get_client("anna+2@example.com")["sub_id"] != panel["sub_id"]


def test_sync_pulls_traffic_and_renames_legacy_client(client):
    _pay(client, "ivan@example.com", 1)
    xui._mock_clients["ivan@example.com"]["traffic"]["up"] = 1024 ** 3
    xui._mock_clients["ivan@example.com"]["traffic"]["down"] = 1024 ** 3
    xui._mock_clients["ivan@example.com"]["client"]["enable"] = False
    xui._mock_clients["ivan@example.com"]["traffic"]["enable"] = False

    page = client.post("/account/sync", follow_redirects=True)
    assert "2 ГБ из 50 ГБ" in page.text
    assert "Отключена" in page.text

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "ivan@example.com").one()
        plan_order = db.query(Order).filter(Order.user_id == user.id).one()
        legacy = f"u{user.id}_o{plan_order.id}"
        created = xui.add_client(legacy, 30, 10, comment="", sub_id="legacy-sub")
        sub = Subscription(
            user_id=user.id,
            email=legacy,
            name="",
            comment="",
            sub_id=created["sub_id"],
            sub_url=created["sub_url"],
            expiry_time=created["expiry_time"],
            traffic_gb=10,
            total_bytes=created["total_bytes"],
            active=True,
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)
        sync_subscription(db, sub)
        db.commit()
        db.refresh(sub)
        assert sub.email == "ivan+2@example.com"
        assert sub.comment == "Старт"
        assert sub.name == "Старт #2"
        assert xui.get_client(legacy) is None
        assert xui.get_client(sub.email)["comment"] == "Старт"
    finally:
        db.close()


def test_cannot_renew_someone_elses_subscription(client):
    _pay(client, "anna@example.com", 1)
    db = SessionLocal()
    try:
        sub_id = db.query(Subscription).one().id
    finally:
        db.close()
    response = client.post(
        "/buy",
        data={
            "email": "other@example.com",
            "plan_id": "1",
            "mode": "renew",
            "subscription_id": str(sub_id),
        },
        follow_redirects=False,
    )
    assert response.status_code == 404


def test_ensure_schema_adds_missing_columns(client):
    from sqlalchemy import text

    with engine_begin() as conn:
        conn.execute(text("ALTER TABLE subscriptions DROP COLUMN comment"))
        conn.execute(text("ALTER TABLE orders DROP COLUMN kind"))
    ensure_schema()
    db = SessionLocal()
    try:
        db.query(Subscription.comment).first()
        db.query(Order.kind).first()
    finally:
        db.close()


def engine_begin():
    from app.database import engine
    return engine.begin()
