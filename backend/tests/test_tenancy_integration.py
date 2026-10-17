"""真实 PostgreSQL 多租户隔离集成测试（review 文档 5.4 第 2 条）。

用 env `TEST_DATABASE_URL` 指向专用测试库（如
`postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/fishstudio_test_tenancy`），
未设置则整模块 skip。测试会 drop/create 该库，**禁止指向任何有数据的库**。

覆盖：
- 用户 B 访问用户 A 的项目详情 / jobs / storyboard / assets / artifact download /
  feedback 全部 403/404；
- 无主项目（user_id NULL）：普通用户拒绝、admin 可访问、claim 后 owner 可访问；
- feedback 跨项目 step_id 拒绝（review 0.3）。
"""

import asyncio
import os
from urllib.parse import urlparse

import pytest

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL 未设置，跳过真实 PG 多租户集成测试"),
    pytest.mark.no_bypass_auth,
]


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


async def _recreate_database() -> None:
    import asyncpg

    conn = await asyncpg.connect(database="postgres", **_admin_dsn())
    try:
        db = _target_db()
        await conn.execute(f'DROP DATABASE IF EXISTS "{db}"')
        await conn.execute(f'CREATE DATABASE "{db}"')
    finally:
        await conn.close()


async def _drop_database() -> None:
    import asyncpg

    conn = await asyncpg.connect(database="postgres", **_admin_dsn())
    try:
        db = _target_db()
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1 AND pid <> pg_backend_pid()",
            db,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{db}"')
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def tenancy():
    """建专用库 + 迁移到 head + 造两个用户/项目，返回 TestClient 与数据句柄。"""
    asyncio.run(_recreate_database())

    mp = pytest.MonkeyPatch()
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.db.database as database
    import app.db.migrations as migrations

    # 1) schema 迁移到 head
    mig_engine = create_async_engine(TEST_DATABASE_URL)
    mp.setattr(migrations, "engine", mig_engine)
    settings = migrations.get_settings()
    mp.setattr(settings, "database_url", TEST_DATABASE_URL)
    asyncio.run(migrations.bootstrap_and_upgrade())

    # 2) app 的 engine/session 指向测试库
    # NullPool：fixture 的 seed 与 TestClient 运行在不同事件循环，
    # 连接不能跨循环复用（否则 asyncpg "another operation is in progress"）
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    mp.setattr(database, "engine", engine)
    mp.setattr(database, "SessionLocal", factory)

    # 3) 跳过 startup 的 init_db（schema 已迁移好）
    async def _noop_init_db():
        return None

    mp.setattr("app.db.init_db.init_db", _noop_init_db)
    mp.setattr("app.main.init_db", _noop_init_db)

    # 4) 造数据：用户 A/B + admin、各自项目、A 的 job/artifact、B 的 run/step、无主项目
    from app.auth.security import create_access_token, hash_password
    from app.db.models import (
        Artifact,
        Episode,
        GenerationJob,
        SeriesProject,
        User,
        WorkflowRun,
        WorkflowStep,
    )

    async def _seed():
        async with factory() as s:
            a = User(username="user_a", password_hash=hash_password("pass-a"), is_admin=False)
            b = User(username="user_b", password_hash=hash_password("pass-b"), is_admin=False)
            admin = User(username="admin_t", password_hash=hash_password("pass-adm"), is_admin=True)
            s.add_all([a, b, admin])
            await s.flush()

            proj_a = SeriesProject(user_id=a.id, title="A 的项目")
            proj_b = SeriesProject(user_id=b.id, title="B 的项目")
            orphan = SeriesProject(user_id=None, title="历史无主项目")
            s.add_all([proj_a, proj_b, orphan])
            await s.flush()
            s.add_all(
                [
                    Episode(project_id=proj_a.id, episode_no=1),
                    Episode(project_id=proj_b.id, episode_no=1),
                    Episode(project_id=orphan.id, episode_no=1),
                ]
            )
            job_a = GenerationJob(
                project_id=proj_a.id,
                operation="generate_video",
                idempotency_key="video:rA:node:shA:attempt0",
                status="succeeded",
            )
            s.add(job_a)
            await s.flush()
            art_a = Artifact(
                project_id=proj_a.id,
                job_id=job_a.id,
                type="video",
                storage_key="videos/a.mp4",
            )
            run_b = WorkflowRun(project_id=proj_b.id)
            s.add_all([art_a, run_b])
            await s.flush()
            step_b = WorkflowStep(run_id=run_b.id, node_key="shot.video.generate", node_type="x")
            s.add(step_b)
            await s.commit()
            return Simple(
                a=a.id,
                b=b.id,
                admin=admin.id,
                proj_a=proj_a.id,
                proj_b=proj_b.id,
                orphan=orphan.id,
                art_a=art_a.id,
                step_b=step_b.id,
            )

    class Simple:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    ids = asyncio.run(_seed())

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        yield Simple(
            client=client,
            ids=ids,
            token_a=create_access_token("user_a"),
            token_b=create_access_token("user_b"),
            token_admin=create_access_token("admin_t"),
        )

    asyncio.run(engine.dispose())
    asyncio.run(mig_engine.dispose())
    mp.undo()
    asyncio.run(_drop_database())


def _h(token):
    return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────
# 跨用户隔离
# ─────────────────────────────────────────


class TestCrossUserIsolation:
    def test_owner_can_read_own_project(self, tenancy):
        r = tenancy.client.get(f"/api/series/projects/{tenancy.ids.proj_a}", headers=_h(tenancy.token_a))
        assert r.status_code == 200

    def test_project_detail_403(self, tenancy):
        r = tenancy.client.get(f"/api/series/projects/{tenancy.ids.proj_a}", headers=_h(tenancy.token_b))
        assert r.status_code == 403

    def test_jobs_404(self, tenancy):
        r = tenancy.client.get(f"/api/jobs?project_id={tenancy.ids.proj_a}", headers=_h(tenancy.token_b))
        assert r.status_code == 404

    def test_storyboard_403(self, tenancy):
        r = tenancy.client.get(
            f"/api/series/projects/{tenancy.ids.proj_a}/storyboard", headers=_h(tenancy.token_b)
        )
        assert r.status_code == 403

    def test_assets_403(self, tenancy):
        r = tenancy.client.get(
            f"/api/series/projects/{tenancy.ids.proj_a}/assets", headers=_h(tenancy.token_b)
        )
        assert r.status_code == 403

    def test_artifact_download_404(self, tenancy):
        r = tenancy.client.get(f"/api/artifacts/{tenancy.ids.art_a}/download", headers=_h(tenancy.token_b))
        assert r.status_code == 404

    def test_feedback_on_others_artifact_403(self, tenancy):
        r = tenancy.client.post(
            f"/api/series/artifacts/{tenancy.ids.art_a}/feedback",
            json={"action": "rated", "score": 5},
            headers=_h(tenancy.token_b),
        )
        assert r.status_code == 403

    def test_no_token_401(self, tenancy):
        r = tenancy.client.get(f"/api/series/projects/{tenancy.ids.proj_a}")
        assert r.status_code == 401


# ─────────────────────────────────────────
# 无主项目（review 0.4）
# ─────────────────────────────────────────


class TestOrphanProject:
    def test_orphan_normal_user_403(self, tenancy):
        r = tenancy.client.get(f"/api/series/projects/{tenancy.ids.orphan}", headers=_h(tenancy.token_a))
        assert r.status_code == 403

    def test_orphan_admin_200(self, tenancy):
        r = tenancy.client.get(f"/api/series/projects/{tenancy.ids.orphan}", headers=_h(tenancy.token_admin))
        assert r.status_code == 200

    def test_claim_then_owner_access(self, tenancy):
        # admin 把无主项目认领给用户 B
        r = tenancy.client.post(
            f"/api/series/projects/{tenancy.ids.orphan}/claim",
            json={"user_id": tenancy.ids.b},
            headers=_h(tenancy.token_admin),
        )
        assert r.status_code == 200, r.text
        assert r.json()["user_id"] == tenancy.ids.b

        # 认领后 owner（B）可访问
        r = tenancy.client.get(f"/api/series/projects/{tenancy.ids.orphan}", headers=_h(tenancy.token_b))
        assert r.status_code == 200

        # 已认领的项目不能重复 claim
        r = tenancy.client.post(
            f"/api/series/projects/{tenancy.ids.orphan}/claim",
            json={"user_id": tenancy.ids.a},
            headers=_h(tenancy.token_admin),
        )
        assert r.status_code == 409

    def test_claim_requires_admin(self, tenancy):
        r = tenancy.client.post(
            f"/api/series/projects/{tenancy.ids.orphan}/claim",
            json={"user_id": tenancy.ids.a},
            headers=_h(tenancy.token_a),
        )
        assert r.status_code == 403


# ─────────────────────────────────────────
# feedback 跨项目 step_id（review 0.3）
# ─────────────────────────────────────────


class TestFeedbackCrossStep:
    def test_cross_project_step_rejected(self, tenancy):
        """A 的 artifact + B 项目的 step_id → 403。"""
        r = tenancy.client.post(
            f"/api/series/artifacts/{tenancy.ids.art_a}/feedback",
            json={"action": "selected", "step_id": tenancy.ids.step_b},
            headers=_h(tenancy.token_a),
        )
        assert r.status_code == 403

    def test_fake_step_rejected(self, tenancy):
        r = tenancy.client.post(
            f"/api/series/artifacts/{tenancy.ids.art_a}/feedback",
            json={"action": "selected", "step_id": "00000000-0000-0000-0000-000000000000"},
            headers=_h(tenancy.token_a),
        )
        assert r.status_code == 400  # step 不存在 → ValueError → 400

    def test_rated_without_step_ok(self, tenancy):
        r = tenancy.client.post(
            f"/api/series/artifacts/{tenancy.ids.art_a}/feedback",
            json={"action": "rated", "score": 4},
            headers=_h(tenancy.token_a),
        )
        assert r.status_code == 200, r.text
