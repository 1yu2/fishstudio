"""剧集（series）路由越权全覆盖：用户 B 带着用户 A 的 id 访问 routers/series.py 的每一条路由。

遍历 series router 里所有带 id 的路由（含 body 里带 project_id 的 /series/approvals），
用 A 的真实 项目 / 分集 / 镜头 / 候选 / 资产 / 产物 / 拉片 / 剪辑版本 id 请求，
B 的响应必须是 403 / 404（现有约定），不能读到也不能改到数据。
路由新增后若没有对应的 id 种子或 body 模板，测试会失败提醒补齐，不会静默漏测。

需要 TEST_DATABASE_URL（专用测试库，会 drop/create）；未设置则整模块 skip。
"""

import asyncio
import os
import re
from urllib.parse import urlparse

import pytest

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL 未设置，跳过真实 PG 越权测试"),
    pytest.mark.no_bypass_auth,
]

# 允许的“拒绝”状态码：现有约定 403（无权）/ 404（不存在或不可见）
DENIED = {403, 404}


def _admin_dsn() -> dict:
    parsed = urlparse(TEST_DATABASE_URL.replace("postgresql+asyncpg", "postgresql"))
    return {
        "user": parsed.username or "postgres",
        "password": parsed.password or "",
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
    }


def _target_db() -> str:
    return urlparse(TEST_DATABASE_URL.replace("postgresql+asyncpg", "postgresql")).path.lstrip("/")


async def _recreate_database(drop_only: bool = False) -> None:
    import asyncpg

    conn = await asyncpg.connect(database="postgres", **_admin_dsn())
    try:
        db = _target_db()
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1 AND pid <> pg_backend_pid()",
            db,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{db}"')
        if not drop_only:
            await conn.execute(f'CREATE DATABASE "{db}"')
    finally:
        await conn.close()


class _Box:
    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest.fixture(scope="module")
def world():
    """专用库 + 迁移到 head + 用户 A/B + A 名下完整的一套剧集数据。"""
    asyncio.run(_recreate_database())
    mp = pytest.MonkeyPatch()
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    import app.db.database as database
    import app.db.migrations as migrations

    mig_engine = create_async_engine(TEST_DATABASE_URL)
    mp.setattr(migrations, "engine", mig_engine)
    mp.setattr(migrations.get_settings(), "database_url", TEST_DATABASE_URL)
    asyncio.run(migrations.bootstrap_and_upgrade())

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    mp.setattr(database, "engine", engine)
    mp.setattr(database, "SessionLocal", factory)

    async def _noop_init_db():
        return None

    mp.setattr("app.db.init_db.init_db", _noop_init_db)
    mp.setattr("app.main.init_db", _noop_init_db)

    from app.auth.security import create_access_token, hash_password
    from app.db.models import (
        Artifact,
        ArtifactCandidate,
        AssetCard,
        Episode,
        ReferenceAnalysis,
        Scene,
        SeriesProject,
        Shot,
        TimelineRevision,
        User,
    )

    async def _seed(owner_name):
        async with factory() as s:
            a = User(username=owner_name, password_hash=hash_password("pass-a"), is_admin=False)
            s.add(a)
            await s.flush()
            project = SeriesProject(user_id=a.id, title="A 的剧集")
            s.add(project)
            await s.flush()
            episode = Episode(project_id=project.id, episode_no=1)
            s.add(episode)
            await s.flush()
            scene = Scene(episode_id=episode.id, scene_no=1)
            s.add(scene)
            await s.flush()
            shot = Shot(scene_id=scene.id, shot_no=1)
            artifact = Artifact(project_id=project.id, type="video", storage_key="videos/a.mp4")
            asset = AssetCard(project_id=project.id, user_id=a.id, type="character", name="A 角色")
            revision = TimelineRevision(
                project_id=project.id, episode_id=episode.id, revision=1, content_hash="h" * 64
            )
            s.add_all([shot, artifact, asset, revision])
            await s.flush()
            candidate = ArtifactCandidate(project_id=project.id, shot_id=shot.id, artifact_id=artifact.id)
            analysis = ReferenceAnalysis(project_id=project.id, artifact_id=artifact.id)
            s.add_all([candidate, analysis])
            from app.db.models import (
                Canvas,
                ModelInvocation,
                ScriptRevision,
                StoryboardRevision,
                WorkflowRun,
                WorkflowStep,
            )

            canvas = Canvas(id=f"canvas-{owner_name}", owner_id=a.id)
            script = ScriptRevision(episode_id=episode.id)
            board = StoryboardRevision(episode_id=episode.id)
            invocation = ModelInvocation(project_id=project.id, request_id="test")
            run = WorkflowRun(project_id=project.id)
            s.add_all([canvas, script, board, invocation, run])
            await s.flush()
            step = WorkflowStep(run_id=run.id, node_key="test", node_type="test")
            s.add(step)
            await s.commit()
            return {
                "canvas_id": canvas.id,
                "based_on_script_revision_id": script.id,
                "target_id": script.id,
                "parent_revision_id": revision.id,
                "storyboard_revision_id": board.id,
                "render_artifact_id": artifact.id,
                "reference_audio_artifact_id": artifact.id,
                "model_invocation_id": invocation.id,
                "step_id": step.id,
                "source_id": project.id,
                "user_id": a.id,
                "project_id": project.id,
                "episode_id": episode.id,
                "shot_id": shot.id,
                "candidate_id": candidate.id,
                "asset_id": asset.id,
                "artifact_id": artifact.id,
                "analysis_id": analysis.id,
                "revision_id": revision.id,
                "kind": "series",
            }

    ids = asyncio.run(_seed("series_a"))
    ids_b = asyncio.run(_seed("series_b"))

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        yield _Box(
            client=client,
            ids=ids,
            ids_b=ids_b,
            headers_a={"Authorization": f"Bearer {create_access_token('series_a')}"},
            headers_b={"Authorization": f"Bearer {create_access_token('series_b')}"},
            app=app,
        )

    asyncio.run(engine.dispose())
    asyncio.run(mig_engine.dispose())
    mp.undo()
    asyncio.run(_recreate_database(drop_only=True))


# ── 自动构造请求 ─────────────────────────────────────────────────────────


def _resolve(schema: dict, spec: dict) -> dict:
    while "$ref" in schema:
        node = spec
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        schema = node
    return schema


def _sample(schema: dict, spec: dict, ids: dict, name: str = ""):
    """按 OpenAPI schema 造一个最小合法值；名字对得上的 id 字段填 A 的真实 id。"""
    schema = _resolve(schema, spec)
    if name in ids:
        return ids[name]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [o for o in schema[key] if o.get("type") != "null"]
            return _sample(options[0], spec, ids, name) if options else None
    if "allOf" in schema:
        return _sample(schema["allOf"][0], spec, ids, name)
    if "enum" in schema:
        return schema["enum"][0]
    if "default" in schema:
        return schema["default"]
    semantic = {
        "target_type": "script",
        "review_type": "script_review",
        "action": "approve",
        "source_type": "series_bible",
        "reference_type": "composition",
        "segment_key": "s1",
    }
    if name in semantic:
        return semantic[name]
    kind = schema.get("type")
    if kind == "object":
        required = set(schema.get("required", [])) | (set(schema.get("properties", {})) & set(ids))
        props = schema.get("properties", {})
        return {k: _sample(props[k], spec, ids, k) for k in required if k in props}
    if kind == "array":
        return []
    if kind == "integer":
        return max(1, int(schema.get("minimum", 1)))
    if kind == "number":
        return max(1.0, float(schema.get("minimum", 1)))
    if kind == "boolean":
        return False
    return "x" * max(1, int(schema.get("minLength", 1)))


# 自动构造不出合法 body 的路由在这里手写；key = (METHOD, 路径模板)
BODY_OVERRIDES: dict = {
    ("PATCH", "/series/projects/{project_id}/workspace-mode"): lambda ids: {"mode": "quick"},
    ("PATCH", "/series/shots/{shot_id}"): lambda ids: {"emotion": "紧张"},
    ("POST", "/series/projects/{project_id}/assets"): lambda ids: {
        "type": "character",
        "name": "越权创建的角色",
    },
}

# 对照组里允许 404 的路由：A 名下确实还没有该资源（尚无 Bible / 剧本修订），不是越权
OWNER_NOT_FOUND_OK = {
    "/series/projects/{project_id}/bibles/{kind}/current",
    "/series/episodes/{episode_id}/scripts/current",
}


def _request(world, method: str, path: str, headers: dict, *, ids=None, foreign=None):
    ids = ids or world.ids
    spec = world.app.openapi()
    full = "/api" + path
    operation = spec["paths"][full][method.lower()]
    url = re.sub(r"\{(\w+)\}", lambda m: str(ids[m.group(1)]), full)
    kwargs: dict = {"headers": headers}
    body = (operation.get("requestBody") or {}).get("content", {})
    if (method, path) in BODY_OVERRIDES:
        kwargs["json"] = BODY_OVERRIDES[(method, path)](ids)
    elif "application/json" in body:
        kwargs["json"] = _sample(body["application/json"]["schema"], spec, ids)
    elif "multipart/form-data" in body:
        kwargs["files"] = {"file": ("a.bin", b"x", "application/octet-stream")}
    # 必填 query 参数（如 reference_audio_artifact_id）：名字对得上 id 的填真实 id，否则占位
    params = {
        p["name"]: ids.get(p["name"], "x")
        for p in operation.get("parameters", [])
        if p["in"] == "query" and (p.get("required") or p["name"] in ids)
    }
    if params:
        kwargs["params"] = params
    # 每次只替换一个 body/query 引用；其余均为 B 的真实资源。
    if path == "/series/approvals":
        kwargs["json"]["target_id"] = ids["episode_id"]
    if path.endswith("/feedback") and method == "POST":
        kwargs["json"]["action"] = "selected"
    if foreign:
        location, name = foreign
        value = (
            world.ids["episode_id"]
            if path == "/series/approvals" and name == "target_id"
            else world.ids[name]
        )
        kwargs.setdefault("json" if location == "body" else "params", {})[name] = value
    return world.client.request(method, url, **kwargs)


# 这些 ID 是全局配置/签名 token，不是用户私有资源；逐路由声明避免未来静默漏测。
EXEMPT_IDS = {
    ("POST", "/series/projects", "body", "workflow_template_id"),
    ("POST", "/series/projects", "body", "active_skill_ids"),
    ("POST", "/series/projects/{project_id}/claim", "body", "user_id"),
    *{
        (
            "POST",
            f"/series/shots/{{shot_id}}/candidates/{{candidate_id}}/{suffix}",
            "body",
            "confirmed_quote_id",
        )
        for suffix in ("regenerate", "regeneration-quote")
    },
    ("POST", "/series/projects/{project_id}/assets/{asset_id}/clone-voice", "body", "confirmed_quote_id"),
}


def _schema_ids(schema, spec):
    schema = _resolve(schema, spec)
    found = set()
    for name, child in schema.get("properties", {}).items():
        if name.endswith(("_id", "_ids")):
            found.add(name)
        found.update(_schema_ids(child, spec))
    for key in ("anyOf", "oneOf", "allOf"):
        for child in schema.get(key, []):
            found.update(_schema_ids(child, spec))
    if "items" in schema:
        found.update(_schema_ids(schema["items"], spec))
    return found


def _id_inventory(spec):
    found = set()
    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            for param in operation.get("parameters", []):
                if param["name"].endswith(("_id", "_ids")):
                    found.add((method.upper(), path, param["in"], param["name"]))
            for content in operation.get("requestBody", {}).get("content", {}).values():
                for name in _schema_ids(content["schema"], spec):
                    found.add((method.upper(), path, "body", name))
    return found


def _series_spec():
    from fastapi import FastAPI

    from app.routers import series

    app = FastAPI()
    app.include_router(series.router)
    return app.openapi()


ID_INVENTORY = _id_inventory(_series_spec())
MIXED_CASES = sorted(item for item in ID_INVENTORY - EXEMPT_IDS if item[2] in {"body", "query"})


def _all_routes():
    return sorted({(method, path) for method, path, _, _ in ID_INVENTORY - EXEMPT_IDS})


ROUTES = _all_routes()


def test_route_list_is_not_empty():
    # 防止遍历静默变成空集
    assert len(ROUTES) >= 50


@pytest.mark.parametrize(("method", "path"), ROUTES, ids=[f"{m} {p}" for m, p in ROUTES])
def test_other_users_series_resources_are_denied(world, method, path):
    """B 访问 A 的剧集 / 分集 / 镜头 / 候选 / 资产 / 产物 / 拉片 / 剪辑版本：只能 403 / 404。"""
    response = _request(world, method, path, world.headers_b)
    assert response.status_code in DENIED, (
        f"{method} {path} 对他人资源返回 {response.status_code}（期望 403/404）：{response.text[:200]}"
    )


@pytest.mark.parametrize(
    ("method", "path"),
    [(m, p) for m, p in ROUTES if m == "GET"],
    ids=[f"{m} {p}" for m, p in ROUTES if m == "GET"],
)
def test_owner_can_read_own_series_resources(world, method, path):
    """对照组：同样的 URL，A 本人读取不应被拒绝（证明上面的 403/404 来自越权而不是构造错误）。"""
    response = _request(world, method, path, world.headers_a)
    if path in OWNER_NOT_FOUND_OK and response.status_code == 404:
        return
    assert response.status_code not in (401, 403, 404, 422) and response.status_code < 500, (
        f"{method} {path} 对本人资源返回 {response.status_code}：{response.text[:200]}"
    )


def test_anonymous_is_rejected_everywhere(world):
    """没有 token：所有带 id 的 series 路由都是 401。"""
    for method, path in ROUTES:
        response = _request(world, method, path, {})
        assert response.status_code == 401, f"{method} {path} 未登录返回 {response.status_code}"


@pytest.mark.parametrize(("method", "path", "location", "name"), MIXED_CASES)
def test_own_path_with_foreign_body_or_query_is_denied(world, method, path, location, name):
    response = _request(world, method, path, world.headers_b, ids=world.ids_b, foreign=(location, name))
    assert response.status_code in DENIED, (
        f"{method} {path} {location}.{name}: {response.status_code} {response.text[:200]}"
    )


def _assert_id_coverage(inventory, ids):
    missing = [item for item in inventory - EXEMPT_IDS if item[3] not in ids]
    assert not missing, f"新增 ID 参数需要补测（种子及混合归属用例）: {missing}"


def test_all_id_parameters_have_coverage(world):
    _assert_id_coverage(ID_INVENTORY, world.ids)


@pytest.mark.parametrize("location", ["body", "query", "path"])
def test_new_id_parameter_requires_coverage(location):
    operation = {"parameters": []}
    if location == "body":
        operation["requestBody"] = {
            "content": {
                "application/json": {
                    "schema": {"type": "object", "properties": {"new_resource_id": {"type": "string"}}}
                }
            }
        }
    else:
        operation["parameters"].append({"in": location, "name": "new_resource_id"})
    inventory = _id_inventory({"paths": {"/series/new": {"post": operation}}})
    assert inventory == {("POST", "/series/new", location, "new_resource_id")}
    with pytest.raises(AssertionError, match="新增 ID 参数需要补测"):
        _assert_id_coverage(inventory, {})
