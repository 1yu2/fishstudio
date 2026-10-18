"""真实 PostgreSQL 迁移集成测试（review P0-2）。

用 env `TEST_DATABASE_URL` 指向专用测试库（如
`postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/fishstudio_test_mig`），
未设置则整模块 skip。测试会 drop/create 该库，**禁止指向任何有数据的库**。

覆盖三类库：
(a) 空库 → upgrade head 后 0001 四表 + 0002 十八表全部存在；
(b) 只有 0001 四表且含历史数据、无 alembic_version 的老库 → bootstrap 后数据保留；
(c) 重复执行 bootstrap 幂等不报错；
(d) head 带数据 → downgrade 0006 → upgrade head，核心数据保留且后续表可重建。
"""

import asyncio
import os
from urllib.parse import urlparse

import pytest

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL 未设置，跳过真实 PG 迁移集成测试"
)

EXPECTED_TABLES = {
    # 0001
    "users",
    "canvases",
    "settings_kv",
    "audit_logs",
    # 0002（18 张）
    "workflow_templates",
    "skill_manifests",
    "series_projects",
    "episodes",
    "scenes",
    "storyboard_revisions",
    "shots",
    "asset_cards",
    "workflow_runs",
    "workflow_steps",
    "workflow_step_dependencies",
    "generation_jobs",
    "artifacts",
    "asset_artifact_refs",
    "shot_asset_refs",
    "approvals",
    "usage_events",
    "artifact_feedback",
    "clone_voice_quotes",
    "script_revisions",
    "series_bible_revisions",
    "style_bible_revisions",
    "invalidation_events",
    "model_invocations",
    "director_plan_revisions",
    "observer_reviews",
    "artifact_candidates",
    "regeneration_requests",
    "timeline_revisions",
    "qc_reports",
    "publications",
    "publish_packages",
    "reference_analyses",
    "shot_references",
    "job_events",
    "provider_circuits",
    "benchmark_datasets",
    "eval_runs",
    "price_table_entries",
    "budget_alerts",
    "publication_metrics",
    # 0021–0022
    "model_deployments",
    "model_routes",
}


def _admin_dsn() -> dict:
    """从 TEST_DATABASE_URL 拆出 asyncpg 直连 postgres 默认库的参数。"""
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
        # alembic 的 sync engine 不断开连接，drop 前先踢掉残留会话
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1 AND pid <> pg_backend_pid()",
            db,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{db}"')
    finally:
        await conn.close()


@pytest.fixture
def mig_env(monkeypatch):
    """重建测试库，并把 app.db.migrations 的 engine / database_url 指向它。"""
    asyncio.run(_recreate_database())

    from sqlalchemy.ext.asyncio import create_async_engine

    import app.db.migrations as migrations

    engine = create_async_engine(TEST_DATABASE_URL)
    monkeypatch.setattr(migrations, "engine", engine)

    settings = migrations.get_settings()
    monkeypatch.setattr(settings, "database_url", TEST_DATABASE_URL)

    yield migrations

    asyncio.run(engine.dispose())
    asyncio.run(_drop_database())


def _tables_and_revision():
    async def _run():
        from sqlalchemy import inspect, text
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(TEST_DATABASE_URL)
        try:
            async with engine.connect() as conn:
                tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
                revision = None
                if "alembic_version" in tables:
                    row = (await conn.execute(text("SELECT version_num FROM alembic_version"))).first()
                    revision = row[0] if row else None
                return tables, revision
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def test_empty_database_upgrades_to_head(mig_env):
    """(a) 空库 → upgrade head → 全部表存在，版本 0024。"""
    asyncio.run(mig_env.bootstrap_and_upgrade())

    tables, revision = _tables_and_revision()
    assert EXPECTED_TABLES <= tables
    assert revision == "0024"

    # 0006 新列：shots 拆分选择槽 + usage_events.cost_source
    def _columns(table):
        async def _run():
            from sqlalchemy import inspect
            from sqlalchemy.ext.asyncio import create_async_engine

            engine = create_async_engine(TEST_DATABASE_URL)
            try:
                async with engine.connect() as conn:
                    return await conn.run_sync(
                        lambda c: {col["name"] for col in inspect(c).get_columns(table)}
                    )
            finally:
                await engine.dispose()

        return asyncio.run(_run())

    assert {"step_attempt", "logical_job_key", "superseded_by"} <= _columns("generation_jobs")
    shot_cols = _columns("shots")
    assert "selected_image_artifact_id" in shot_cols
    assert "selected_video_artifact_id" in shot_cols
    assert "selected_artifact_id" not in shot_cols
    assert "cost_source" in _columns("usage_events")
    assert "based_on_script_revision_id" in _columns("storyboard_revisions")
    assert {"series_bible_revision_id", "style_bible_revision_id"} <= _columns("episodes")
    assert {
        "based_on_series_bible_revision_id",
        "based_on_style_bible_revision_id",
    } <= _columns("script_revisions")
    assert {"narrative_purpose", "generation_mode", "qa_status"} <= _columns("shots")
    assert {"priority", "worker_class", "leased_by", "heartbeat_at", "dead_lettered_at"} <= _columns(
        "generation_jobs"
    )
    assert "storage_backend" in _columns("artifacts")


def test_legacy_0001_database_stamps_0001_then_upgrades(mig_env):
    """(b) 无 alembic_version、仅有 0001 四表的老库 → stamp 0001 + upgrade head。"""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from alembic import command

    # 手工构造 0001 老库：跑 0001 迁移后删掉 alembic_version，模拟历史 create_all 库
    cfg = mig_env._get_alembic_config()
    command.upgrade(cfg, "0001")

    async def _seed_legacy_data_and_drop_version_table():
        engine = create_async_engine(TEST_DATABASE_URL)
        try:
            async with engine.begin() as conn:
                user_id = (
                    await conn.execute(
                        text(
                            "INSERT INTO users (username, password_hash, is_admin) "
                            "VALUES ('legacy-user', 'legacy-hash', false) RETURNING id"
                        )
                    )
                ).scalar_one()
                await conn.execute(
                    text(
                        "INSERT INTO canvases (id, name, created_at_ts, owner_id) "
                        "VALUES ('legacy-canvas', '历史画布', 123.5, :owner_id)"
                    ),
                    {"owner_id": user_id},
                )
                await conn.execute(text("DROP TABLE alembic_version"))
        finally:
            await engine.dispose()

    asyncio.run(_seed_legacy_data_and_drop_version_table())

    asyncio.run(mig_env.bootstrap_and_upgrade())

    tables, revision = _tables_and_revision()
    assert EXPECTED_TABLES <= tables
    assert revision == "0024"

    async def _legacy_rows():
        engine = create_async_engine(TEST_DATABASE_URL)
        try:
            async with engine.connect() as conn:
                username = (
                    await conn.execute(text("SELECT username FROM users WHERE username='legacy-user'"))
                ).scalar_one()
                canvas = (
                    await conn.execute(text("SELECT name FROM canvases WHERE id='legacy-canvas'"))
                ).scalar_one()
                return username, canvas
        finally:
            await engine.dispose()

    assert asyncio.run(_legacy_rows()) == ("legacy-user", "历史画布")


def test_bootstrap_is_idempotent(mig_env):
    """(c) 重复执行 bootstrap_and_upgrade 幂等不报错。"""

    async def _run_twice():
        # 同一个 async engine 不能跨 asyncio.run 的事件循环复用，合并到一个 loop
        await mig_env.bootstrap_and_upgrade()
        await mig_env.bootstrap_and_upgrade()

    asyncio.run(_run_twice())

    tables, revision = _tables_and_revision()
    assert EXPECTED_TABLES <= tables
    assert revision == "0024"


def test_head_downgrade_and_upgrade_preserves_core_data(mig_env):
    """0007～0010 可降至 0006 再回升，既有项目数据不丢失。"""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from alembic import command

    asyncio.run(mig_env.bootstrap_and_upgrade())

    async def _seed():
        engine = create_async_engine(TEST_DATABASE_URL)
        try:
            async with engine.begin() as conn:
                user_id = (
                    await conn.execute(
                        text(
                            "INSERT INTO users (username, password_hash, is_admin) "
                            "VALUES ('roundtrip-user', 'roundtrip-hash', false) RETURNING id"
                        )
                    )
                ).scalar_one()
                await conn.execute(
                    text(
                        "INSERT INTO series_projects (id, user_id, title) "
                        "VALUES ('roundtrip-project', :user_id, '迁移回归项目')"
                    ),
                    {"user_id": user_id},
                )
        finally:
            await engine.dispose()

    asyncio.run(_seed())
    cfg = mig_env._get_alembic_config()
    command.downgrade(cfg, "0006")
    tables, revision = _tables_and_revision()
    assert revision == "0006"
    assert "clone_voice_quotes" not in tables

    command.upgrade(cfg, "head")
    tables, revision = _tables_and_revision()
    assert revision == "0024"
    assert "clone_voice_quotes" in tables
    assert "script_revisions" in tables
    assert "series_bible_revisions" in tables
    assert "style_bible_revisions" in tables
    assert "invalidation_events" in tables

    async def _project_title():
        engine = create_async_engine(TEST_DATABASE_URL)
        try:
            async with engine.connect() as conn:
                return (
                    await conn.execute(text("SELECT title FROM series_projects WHERE id='roundtrip-project'"))
                ).scalar_one()
        finally:
            await engine.dispose()

    assert asyncio.run(_project_title()) == "迁移回归项目"


def test_model_route_tiers_upgrade_and_downgrade(mig_env):
    """0023：同一用途可按 draft/preview/final 分别绑定；降级只保留 final 档。"""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from alembic import command

    cfg = mig_env._get_alembic_config()
    command.upgrade(cfg, "0022")

    async def _exec(*statements):
        engine = create_async_engine(TEST_DATABASE_URL)
        try:
            async with engine.begin() as conn:
                results = [await conn.execute(text(sql)) for sql in statements]
                return [row for row in results[-1]] if results[-1].returns_rows else None
        finally:
            await engine.dispose()

    insert = (
        "INSERT INTO model_routes (purpose, {extra}modality, primary_deployment, fallback_deployments) "
        "VALUES ('shot_video', {value}'video', '{primary}', '[]'::jsonb)"
    )
    asyncio.run(_exec(insert.format(extra="", value="", primary="seedance")))

    command.upgrade(cfg, "head")
    assert asyncio.run(_exec("SELECT purpose, tier, primary_deployment FROM model_routes")) == [
        ("shot_video", "final", "seedance")
    ]
    asyncio.run(_exec(insert.format(extra="tier, ", value="'preview', ", primary="local-h3")))
    rows = asyncio.run(_exec("SELECT tier, primary_deployment FROM model_routes ORDER BY tier"))
    assert rows == [("final", "seedance"), ("preview", "local-h3")]

    command.downgrade(cfg, "0022")
    assert asyncio.run(_exec("SELECT purpose, primary_deployment FROM model_routes")) == [
        ("shot_video", "seedance")
    ]
