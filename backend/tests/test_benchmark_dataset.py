"""基准集 v1（ViStoryBench 19 个公版故事）：数据完整性 + 入库/版本化语义。

数据完整性测试不需要数据库；入库测试需要 env `TEST_DATABASE_URL` 指向专用测试库
（会 drop/create 该库），未设置则 skip。
"""

import asyncio
import os
import re
from urllib.parse import urlparse

import pytest

from app.services import eval_service

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


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
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname=$1 AND pid <> pg_backend_pid()",
            db,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{db}"')
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def pg_factory():
    """建专用库 + 迁移到 head，返回 async_sessionmaker；结束 drop 库。"""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL 未设置")
    asyncio.run(_recreate_database())

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    import app.db.migrations as migrations

    mp = pytest.MonkeyPatch()
    mig_engine = create_async_engine(TEST_DATABASE_URL)
    mp.setattr(migrations, "engine", mig_engine)
    settings = migrations.get_settings()
    mp.setattr(settings, "database_url", TEST_DATABASE_URL)
    asyncio.run(migrations.bootstrap_and_upgrade())

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory

    asyncio.run(engine.dispose())
    asyncio.run(mig_engine.dispose())
    mp.undo()
    asyncio.run(_drop_database())


# ---------- 数据完整性（无需数据库） ----------


def test_benchmark_file_is_complete_and_self_consistent():
    stories = eval_service.benchmark_stories()
    assert len(stories) == 19
    assert len({story["key"] for story in stories}) == 19
    assert sum(len(story["shots"]) for story in stories) == 293
    sheet_paths = []
    for story in stories:
        assert story["source"]["license"] == "MIT"
        assert story["aspect_ratio"] == "9:16"
        assert story["shots"], story["key"]
        character_keys = {character["key"] for character in story["characters"]}
        assert len(character_keys) == len(story["characters"]), story["key"]
        for character in story["characters"]:
            assert character["prompt"].strip(), (story["key"], character["key"])
            if character["reference_image"]:
                sheet_paths.append(character["reference_image"])
                assert re.fullmatch(
                    r"benchmarks/vistory/\d{2}/[a-z0-9-]+\.jpeg", character["reference_image"]
                )
        for shot in story["shots"]:
            assert shot["description"].strip() and shot["duration_ms"] > 0
            assert set(shot["characters"]) <= character_keys, (story["key"], shot["key"])
        assert sum(1 for c in story["characters"] if c["reference_image"]) <= 3
    assert len(sheet_paths) == len(set(sheet_paths)) == 47


def test_dataset_payload_hash_is_stable():
    story = eval_service.benchmark_stories()[0]
    first = eval_service._dataset_payload(story)
    second = eval_service._dataset_payload(story)
    assert eval_service._canonical_hash(first) == eval_service._canonical_hash(second)
    inputs, constraints = first
    assert constraints["shot_count"] == len(inputs["shots"])
    assert set(constraints["required_metrics"]) == set(eval_service.QUALITY_DIMENSIONS)


# ---------- 入库 / 版本化（真实 PG） ----------


def _run(coro):
    return asyncio.run(coro)


def test_seed_inserts_all_stories_and_retires_placeholders(pg_factory):
    from sqlalchemy import select

    from app.db.models import BenchmarkDataset

    async def scenario():
        async with pg_factory() as s:
            s.add(
                BenchmarkDataset(
                    key="short-drama-3-shot",
                    version=1,
                    name="old",
                    shot_count=3,
                    input_payload={},
                    expected_constraints={},
                    content_hash="x",
                    status="active",
                )
            )
            await s.commit()
        async with pg_factory() as s:
            active = await eval_service.seed_benchmarks(s)
        async with pg_factory() as s:
            again = await eval_service.seed_benchmarks(s)
            placeholder = await s.scalar(
                select(BenchmarkDataset).where(BenchmarkDataset.key == "short-drama-3-shot")
            )
            total = len((await s.execute(select(BenchmarkDataset))).scalars().all())
        return active, again, placeholder, total

    active, again, placeholder, total = _run(scenario())
    assert len(active) == 19 and all(row.status == "active" and row.version == 1 for row in active)
    assert [row.id for row in again] == [row.id for row in active]  # 幂等
    assert placeholder.status == "retired"
    assert total == 20


def test_changed_content_gets_new_version_and_old_runs_stay_pinned(pg_factory, monkeypatch):
    from sqlalchemy import select

    from app.db.models import BenchmarkDataset

    stories = eval_service.benchmark_stories()
    first_key = stories[0]["key"]
    edited = ({**stories[0], "shots": stories[0]["shots"][:-1]},) + stories[1:]

    async def scenario():
        async with pg_factory() as s:
            await eval_service.seed_benchmarks(s)
            v1 = await s.scalar(
                select(BenchmarkDataset).where(
                    BenchmarkDataset.key == first_key, BenchmarkDataset.version == 1
                )
            )
            samples = [{"metrics": {}} for _ in range(v1.shot_count)]
        monkeypatch.setattr(eval_service, "benchmark_stories", lambda: edited)
        async with pg_factory() as s:
            active = await eval_service.seed_benchmarks(s)
        async with pg_factory() as s:
            old = await s.get(BenchmarkDataset, v1.id)
            with pytest.raises(ValueError, match="已停用"):
                await eval_service.create_eval_run(
                    s, dataset=old, provider="p", deployment="d", model="m", samples=samples
                )
        return v1, old, active

    v1, old, active = _run(scenario())
    new = next(row for row in active if row.key == first_key)
    assert old.status == "retired" and old.content_hash == v1.content_hash
    assert new.version == 2 and new.shot_count == v1.shot_count - 1
    assert len(active) == 19
