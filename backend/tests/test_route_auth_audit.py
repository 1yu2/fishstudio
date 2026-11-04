"""接口鉴权审计：遍历 FastAPI app 的所有路由，不带 token 请求必须被拒绝。

以后新增的接口如果忘了加鉴权（get_current_user / 路由器级依赖），这个测试会失败。
确需匿名访问的接口必须写进 ANONYMOUS_ALLOWLIST 并注明理由。
"""

import os
import re

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

pytestmark = pytest.mark.no_bypass_auth

# 未带 token 时允许的状态码：鉴权拒绝，或路由本身对该请求形态不适用
REJECTED = {401, 403, 404, 405, 422}

# (方法, 路径模板) -> 允许匿名访问的理由
ANONYMOUS_ALLOWLIST: dict[tuple[str, str], str] = {
    ("GET", "/"): "根路径，只返回固定的服务名与状态，不含任何数据",
    ("GET", "/health"): "活探针，负载均衡 / K8s 必须能匿名访问，不查库不返回数据",
    ("GET", "/ready"): "就绪探针，同上；只返回 DB 连通性",
    ("POST", "/api/auth/login"): "登录入口，本身就是拿 token 的地方",
    ("GET", "/storage/{path:path}"): (
        "自带手工鉴权：public/ 前缀为公共素材可匿名读；其余路径无 token 返回 401、"
        "无权限返回 404（见 app/main.py serve_storage）。本测试对它单独断言"
    ),
}

# docs / openapi 页面由 FastAPI 自带，不属于业务接口：只放行 app 自己配置的那几个精确路径（见 framework_paths）


def _fill(path: str) -> str:
    """把 {param} 换成占位值，/ {x:path} 也适用。"""
    return re.sub(r"\{[^}]+\}", "x", path)


# 允许存在的 Mount（挂载点前缀 → 理由）。其余 Mount 要么是带 routes 的子应用（递归审计），要么必须登记在这里
MOUNT_ALLOWLIST: dict[str, str] = {}


def framework_paths(app) -> set[str]:
    """该 app 自己实际配置的文档 / openapi 路径（精确匹配，不做后缀匹配；挂载子应用的同名路径不在其中）。"""
    configured = (
        app.docs_url,
        app.redoc_url,
        app.openapi_url,
        getattr(app, "swagger_ui_oauth2_redirect_url", None),
    )
    return {path for path in configured if path}


def walk_routes(routes, prefix=""):
    """递归展开 Mount / 子 Router，产出 (kind, method, full_path, route)。

    kind: http | websocket | opaque_mount（无法展开的挂载，如 StaticFiles）。
    """
    for route in routes:
        if isinstance(route, Mount):
            full = prefix + route.path
            inner = getattr(route.app, "routes", None)
            if inner is None:
                yield "opaque_mount", "", full, route
            else:
                yield from walk_routes(inner, full)
        elif isinstance(route, (APIWebSocketRoute, WebSocketRoute)):
            yield "websocket", "WS", prefix + route.path, route
        elif isinstance(route, (APIRoute, Route)):
            for method in sorted((route.methods or set()) - {"HEAD", "OPTIONS"}):
                yield "http", method, prefix + route.path, route


def _all_api_routes(app):
    for kind, method, path, _route in walk_routes(app.routes):
        if kind == "http":
            yield method, path


def _requires_login(dependant) -> bool:
    """依赖树（含路由器级依赖）里是否有 get_current_user / require_admin。"""
    from app.auth.dependencies import get_current_user, require_admin

    if dependant.call in (get_current_user, require_admin):
        return True
    return any(_requires_login(sub) for sub in dependant.dependencies)


def audit_app(app, *, allowlist=None, mount_allowlist=None) -> list[str]:
    """返回所有未鉴权的问题描述；空列表代表通过。覆盖 HTTP、WebSocket 与挂载的子应用。"""
    allowlist = ANONYMOUS_ALLOWLIST if allowlist is None else allowlist
    mount_allowlist = MOUNT_ALLOWLIST if mount_allowlist is None else mount_allowlist
    problems: list[str] = []
    framework = framework_paths(app)
    old = os.environ.get("WS_AUTH_DISABLED")
    os.environ["WS_AUTH_DISABLED"] = "false"  # 生产语义：WebSocket 必须真的鉴权
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            for kind, method, path, route in walk_routes(app.routes):
                if path in framework or (method, path) in allowlist:
                    continue
                if kind == "opaque_mount":
                    if path not in mount_allowlist:
                        problems.append(f"MOUNT {path}: 无法展开的挂载且不在白名单")
                    continue
                if kind == "websocket":
                    try:
                        with client.websocket_connect(_fill(path)):
                            problems.append(f"WS {path}: 匿名连接被接受")
                    except WebSocketDisconnect:
                        pass
                    except Exception:  # 握手被拒绝（如 403/404）也算拒绝
                        pass
                    continue
                response = client.request(method, _fill(path))
                if response.status_code not in REJECTED:
                    problems.append(f"{method} {path} -> {response.status_code}")
                dependant = getattr(route, "dependant", None)
                if dependant is None or not _requires_login(dependant):
                    problems.append(f"{method} {path}: 依赖树里没有登录依赖")
    finally:
        if old is None:
            os.environ.pop("WS_AUTH_DISABLED", None)
        else:
            os.environ["WS_AUTH_DISABLED"] = old
    return problems


@pytest.fixture
def anon_client():
    from app.main import app

    app.dependency_overrides.clear()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_all_routes_reject_anonymous():
    from app.main import app

    app.dependency_overrides.clear()
    assert len(list(_all_api_routes(app))) > 50, "路由数量异常少，审计可能没有遍历到全部路由"
    problems = audit_app(app)
    assert not problems, "以下接口未带 token 也能访问（请加 get_current_user 或登记白名单）:\n" + "\n".join(
        problems
    )


def test_audit_flags_anonymous_routes_in_mounted_subapp():
    """自检：挂载的子应用里新增匿名 HTTP / WebSocket 路由，审计必须能发现。"""
    from fastapi import APIRouter, Depends, FastAPI
    from fastapi.staticfiles import StaticFiles

    from app.auth.dependencies import get_current_user

    guarded = APIRouter(dependencies=[Depends(get_current_user)])

    @guarded.get("/api/ok")
    async def ok():
        return {}

    sub = FastAPI()

    @sub.get("/open")
    async def open_route():
        return {"leak": True}

    @sub.websocket("/live")
    async def live(websocket: WebSocket):
        await websocket.accept()
        await websocket.close()

    test_app = FastAPI()
    test_app.include_router(guarded)
    test_app.mount("/api/import", sub)
    test_app.mount("/static", StaticFiles(directory="."), name="static")

    problems = "\n".join(audit_app(test_app, allowlist={}))
    assert "/api/import/open" in problems
    assert "/api/import/live" in problems
    assert "MOUNT /static" in problems
    # 业务路径恰好叫 docs / openapi.json 时不能被当作框架文档放过（不能按后缀匹配）
    assert "GET /api/import/docs" in problems
    assert "GET /api/import/openapi.json" in problems
    assert "/api/ok" not in problems  # 正常受保护的路由不应误报


def test_every_route_declares_login_dependency():
    """结构性检查（含挂载子应用）：404 之类的状态码可能掩盖“其实没鉴权”，所以再核对依赖树本身。"""
    from app.main import app

    missing = []
    for kind, method, path, route in walk_routes(app.routes):
        if kind != "http" or path in framework_paths(app) or (method, path) in ANONYMOUS_ALLOWLIST:
            continue
        dependant = getattr(route, "dependant", None)
        if dependant is None or not _requires_login(dependant):
            missing.append(f"{method} {path}")
    assert not missing, "以下接口没有登录依赖:\n" + "\n".join(sorted(missing))


def test_allowlist_entries_still_exist():
    """白名单不能留无效条目（接口改名 / 删除后应同步清理）。"""
    from app.main import app

    existing = set(_all_api_routes(app))
    stale = [key for key in ANONYMOUS_ALLOWLIST if key not in existing]
    assert not stale, f"白名单里的接口已不存在: {stale}"


def test_storage_requires_token_except_public(anon_client):
    assert anon_client.get("/storage/images/a.png").status_code == 401
    assert anon_client.get("/storage/videos/a.mp4").status_code == 401
    assert anon_client.get("/storage/public/not-exist.png").status_code == 404


def test_websocket_requires_token():
    """WebSocket 不在 HTTP 路由里：无 token 必须被关闭（WS_AUTH_DISABLED 只用于测试）。"""
    import os

    from starlette.websockets import WebSocket, WebSocketDisconnect

    from app.main import app

    old = os.environ.get("WS_AUTH_DISABLED")
    os.environ["WS_AUTH_DISABLED"] = "false"
    try:
        app.dependency_overrides.clear()
        with TestClient(app) as client:
            with pytest.raises(WebSocketDisconnect) as info:
                with client.websocket_connect("/ws/some-canvas"):
                    pass
            assert info.value.code == 4401
    finally:
        if old is None:
            os.environ.pop("WS_AUTH_DISABLED", None)
        else:
            os.environ["WS_AUTH_DISABLED"] = old


def test_artifact_download_and_upload_endpoints_require_login(anon_client):
    for method, path in [
        ("GET", "/api/artifacts/x/download"),
        ("POST", "/api/upload-image"),
        ("POST", "/api/upload-audio"),
        ("POST", "/api/upload-video"),
    ]:
        assert anon_client.request(method, path).status_code == 401, (method, path)
