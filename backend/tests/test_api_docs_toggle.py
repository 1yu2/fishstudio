"""/docs、/redoc、/openapi.json 默认关闭，API_DOCS_ENABLED=true 才开放。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import api_docs_enabled, fastapi_docs_kwargs

DOC_PATHS = ("/docs", "/redoc", "/openapi.json")


def _client() -> TestClient:
    # 与 app.main 同一套参数：用最小 app 验证开关，避免重新加载整个应用
    return TestClient(FastAPI(**fastapi_docs_kwargs()))


def test_docs_disabled_by_default(monkeypatch):
    monkeypatch.delenv("API_DOCS_ENABLED", raising=False)
    assert api_docs_enabled() is False
    client = _client()
    assert [client.get(path).status_code for path in DOC_PATHS] == [404, 404, 404]


@pytest.mark.parametrize("value", ["", "false", "0", "no", "off"])
def test_docs_stay_closed_for_falsey_values(monkeypatch, value):
    monkeypatch.setenv("API_DOCS_ENABLED", value)
    client = _client()
    assert [client.get(path).status_code for path in DOC_PATHS] == [404, 404, 404]


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes"])
def test_docs_open_when_enabled(monkeypatch, value):
    monkeypatch.setenv("API_DOCS_ENABLED", value)
    client = _client()
    assert [client.get(path).status_code for path in DOC_PATHS] == [200, 200, 200]


def test_real_app_has_no_docs_routes_by_default():
    # 测试环境没有设置 API_DOCS_ENABLED：真实 app 不挂文档路由
    from app.main import app

    assert app.docs_url is None and app.redoc_url is None and app.openapi_url is None
    paths = {getattr(route, "path", "") for route in app.routes}
    assert not paths & set(DOC_PATHS)
