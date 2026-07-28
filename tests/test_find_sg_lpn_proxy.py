import json
import os

import pytest
from fastapi import HTTPException

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("DN_CONTACTS_API_BASE_URL", "https://example.test")
os.environ.setdefault("DN_CONTACTS_HW_ID", "test-hw-id")
os.environ.setdefault("DN_CONTACTS_APP_KEY", "test-app-key")

from app.api import find_sg_lpn  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_find_sg_lpn_proxy_adds_upstream_credentials(monkeypatch):
    captured = {}

    monkeypatch.setattr(find_sg_lpn.settings, "find_sg_lpn_url", "https://example.test/findSgLpnInfos")
    monkeypatch.setattr(find_sg_lpn.settings, "find_sg_lpn_hw_id", "test-hw-id")
    monkeypatch.setattr(find_sg_lpn.settings, "find_sg_lpn_appkey", "secret-app-key")
    monkeypatch.setattr(find_sg_lpn.settings, "find_sg_lpn_timeout_seconds", 3)

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse({"code": "200", "list": [{"lpn_name": "LPN-1"}]})

    monkeypatch.setattr(find_sg_lpn, "urlopen", fake_urlopen)

    response = find_sg_lpn.find_sg_lpn_infos(
        find_sg_lpn.FindSgLpnRequest(order_number=" SO-123 ", pageSize=50, pageNum=2)
    )

    assert response == {"code": "200", "list": [{"lpn_name": "LPN-1"}]}
    assert captured["url"] == "https://example.test/findSgLpnInfos"
    assert captured["headers"]["X-hw-id"] == "test-hw-id"
    assert captured["headers"]["X-hw-appkey"] == "secret-app-key"
    assert captured["body"] == {"order_number": "SO-123", "pageSize": 50, "pageNum": 2}
    assert captured["timeout"] == 3


def test_find_sg_lpn_proxy_requires_server_appkey(monkeypatch):
    monkeypatch.setattr(find_sg_lpn.settings, "find_sg_lpn_appkey", "")

    with pytest.raises(HTTPException) as exc_info:
        find_sg_lpn.find_sg_lpn_infos(find_sg_lpn.FindSgLpnRequest(order_number="SO-123"))

    assert exc_info.value.status_code == 503
