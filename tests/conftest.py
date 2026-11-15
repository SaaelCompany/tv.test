import os
import tempfile

_db = tempfile.NamedTemporaryFile(prefix="lotvpn-", suffix=".db", delete=False)
os.environ["DATABASE_URL"] = "sqlite:///" + _db.name
os.environ["XUI_MOCK_MODE"] = "true"
os.environ["ROBOKASSA_MOCK"] = "true"
os.environ["SECRET_KEY"] = ""
os.environ["XUI_SYNC_INTERVAL"] = "0"
os.environ["DEBUG"] = "false"
os.environ["COOKIE_SECURE"] = "false"
os.environ["BASE_URL"] = "http://testserver"
os.environ["ADMIN_PASSWORD"] = "test-admin"
os.environ["XUI_SUB_BASE_URL"] = "http://panel.example:2096"
os.environ["XUI_URL"] = "http://127.0.0.1:2053"

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app
from app.xui_client import xui


@pytest.fixture
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    xui.reset_mock()
    with TestClient(app) as test_client:
        yield test_client
