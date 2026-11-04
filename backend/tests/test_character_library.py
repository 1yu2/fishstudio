"""角色库：上传 / 列表隔离 / 改名删除 / 存入角色库 / 加入项目（真实 PG）。"""

import asyncio
import io
import os
from urllib.parse import urlparse

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from app.services import character_library_service as service
from app.services import node_canvas_service as canvas_service

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
requires_pg = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL 未设置，跳过真实 PG 工作台测试"
)


async def _recreate_database(drop_only: bool = False) -> None:
    import asyncpg

    parsed = urlparse(TEST_DATABASE_URL.replace("postgresql+asyncpg", "postgresql"))
    db = parsed.path.lstrip("/")
    conn = await asyncpg.connect(
        database="postgres",
        user=parsed.username or "postgres",
        password=parsed.password or "",
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
    )
    try:
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1 AND pid <> pg_backend_pid()",
            db,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{db}"')
        if not drop_only:
            await conn.execute(f'CREATE DATABASE "{db}"')
    finally:
        await conn.close()


@pytest.fixture
def pg(monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL 未设置")
    asyncio.run(_recreate_database())
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    import app.db.migrations as migrations

    mp = pytest.MonkeyPatch()
    mig_engine = create_async_engine(TEST_DATABASE_URL)
    mp.setattr(migrations, "engine", mig_engine)
    mp.setattr(migrations.get_settings(), "database_url", TEST_DATABASE_URL)
    asyncio.run(migrations.bootstrap_and_upgrade())
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(engine.dispose())
    asyncio.run(mig_engine.dispose())
    mp.undo()
    asyncio.run(_recreate_database(drop_only=True))


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    """上传文件写到临时目录，不污染 backend/storage。"""
    from app.services import asset_service

    monkeypatch.setattr(asset_service, "STORAGE_DIR", tmp_path)
    return tmp_path


def _file(data=PNG, name="fox.png", mime="image/png"):
    return UploadFile(file=io.BytesIO(data), filename=name, headers=Headers({"content-type": mime}))


async def _user(session, name):
    from app.db.models import User

    user = User(username=name, password_hash="x")
    session.add(user)
    await session.flush()
    return user


async def _canvas(session, user, nodes, edges=()):
    canvas = await canvas_service.create_canvas(session, user, title="雪夜")
    canvas.graph = {"nodes": nodes, "edges": list(edges)}
    await session.flush()
    return canvas


def _node(node_id, name=None, look="", selected=None, **extra):
    data = {
        "label": f"角色 · {name}",
        "role": "character",
        "character": name,
        "look": look,
        "prompt": f"{name}的外貌",
    }
    if selected:
        data["selected"] = selected
    return {
        "id": node_id,
        "type": "image",
        "position": {"x": 0, "y": len(node_id) * 10},
        "data": {**data, **extra},
    }


async def _artifact(session, canvas, label="a", node_id=None):
    """项目里的图片；node_id 给了就是该节点的上传 / 复制结果（metadata.canvas_node_id）。"""
    from app.db.models import Artifact

    artifact = Artifact(
        project_id=canvas.project_id,
        type="image",
        storage_key=f"x/{label}.png",
        mime_type="image/png",
        width=512,
        height=768,
        artifact_metadata={"source": "canvas_upload", "canvas_node_id": node_id} if node_id else {},
    )
    session.add(artifact)
    await session.flush()
    return artifact


async def _status(coro):
    try:
        return await coro
    except HTTPException as error:
        return error.status_code, error.detail


@requires_pg
def test_upload_validates_type_and_size_and_lists_only_own(pg):
    from app.routers import character_library as router

    async def _run():
        async with pg() as s:
            alice = await _user(s, "alice")
            bob = await _user(s, "bob")
            gif = await _status(
                router.upload_character(
                    name="狐妖",
                    description="",
                    file=_file(b"GIF89a" + b"0" * 32, "a.gif", "image/gif"),
                    session=s,
                    user=alice,
                )
            )
            # 客户端声称 image/png，实际是文本：按文件头判断，拒绝
            fake_png = await _status(
                router.upload_character(
                    name="狐妖",
                    description="",
                    file=_file(b"just some text", "a.png", "image/png"),
                    session=s,
                    user=alice,
                )
            )
            too_big = await _status(
                router.upload_character(
                    name="狐妖",
                    description="",
                    file=_file(b"0" * (10 * 1024 * 1024 + 1)),
                    session=s,
                    user=alice,
                )
            )
            empty = await _status(
                router.upload_character(name="狐妖", description="", file=_file(b""), session=s, user=alice)
            )
            no_name = await _status(
                router.upload_character(name="  ", description="", file=_file(), session=s, user=alice)
            )
            made = await router.upload_character(
                name="狐妖", description="白发", file=_file(), session=s, user=alice
            )
            mine = await router.list_characters(canvas_id=None, session=s, user=alice)
            theirs = await router.list_characters(canvas_id=None, session=s, user=bob)
            return (gif, fake_png), too_big, empty, no_name, made, mine, theirs

    bad_type, too_big, empty, no_name, made, mine, theirs = asyncio.run(_run())
    assert bad_type[0] == (400, "只支持 PNG / JPG / WebP 图片")
    assert bad_type[1][0] == 400
    assert too_big[0] == empty[0] == no_name[0] == 422
    assert [item["name"] for item in mine] == ["狐妖"] and theirs == []
    assert made["description"] == "白发" and made["looks"][0]["name"] == "默认"
    assert (
        made["cover_url"]
        == made["looks"][0]["url"]
        == f"/api/artifacts/{made['looks'][0]['artifact_id']}/download"
    )
    assert made["in_project"] is False


@requires_pg
def test_library_upload_downloadable_only_by_owner(pg, storage, monkeypatch):
    from app.db.models import Artifact
    from app.routers import artifacts as artifacts_router
    from app.routers import character_library as router

    monkeypatch.setattr(artifacts_router, "_STORAGE_ROOT", storage.resolve())

    async def _run():
        async with pg() as s:
            alice = await _user(s, "alice")
            bob = await _user(s, "bob")
            made = await router.upload_character(
                name="狐妖", description="", file=_file(), session=s, user=alice
            )
            artifact_id = made["looks"][0]["artifact_id"]
            artifact = await s.get(Artifact, artifact_id)
            ok = await artifacts_router.download_artifact(artifact_id, session=s, user=alice)
            denied = await _status(artifacts_router.download_artifact(artifact_id, session=s, user=bob))
            return artifact, ok, denied

    artifact, ok, denied = asyncio.run(_run())
    assert artifact.project_id is None and artifact.artifact_metadata["source"] == "library_upload"
    assert ok.status_code == 200
    assert denied[0] == 404


@requires_pg
def test_rename_delete_and_foreign_access_is_404(pg):
    from app.db.models import Artifact
    from app.routers import character_library as router

    async def _run():
        async with pg() as s:
            alice = await _user(s, "alice")
            bob = await _user(s, "bob")
            made = await router.upload_character(
                name="狐妖", description="", file=_file(), session=s, user=alice
            )
            patched = await router.update_character(
                made["id"], router.CharacterPatch(name="九尾", description="新描述"), session=s, user=alice
            )
            blank = await _status(
                router.update_character(made["id"], router.CharacterPatch(name=" "), session=s, user=alice)
            )
            foreign_patch = await _status(
                router.update_character(made["id"], router.CharacterPatch(name="x"), session=s, user=bob)
            )
            foreign_delete = await _status(router.delete_character(made["id"], session=s, user=bob))
            await router.delete_character(made["id"], session=s, user=alice)
            gone = await _status(router.delete_character(made["id"], session=s, user=alice))
            left = await s.get(Artifact, made["looks"][0]["artifact_id"])
            return (
                patched,
                blank,
                foreign_patch,
                foreign_delete,
                gone,
                left,
                await router.list_characters(None, s, alice),
            )

    patched, blank, foreign_patch, foreign_delete, gone, left, listed = asyncio.run(_run())
    assert (patched["name"], patched["description"]) == ("九尾", "新描述")
    assert blank[0] == 422
    assert foreign_patch[0] == foreign_delete[0] == gone[0] == 404
    assert left is not None  # 只删资产卡，图片保留
    assert listed == []


@requires_pg
def test_save_to_library_creates_then_updates_and_requires_final(pg):
    from app.routers import character_library as router

    async def _run():
        async with pg() as s:
            user = await _user(s, "u")
            other = await _user(s, "other")
            canvas = await _canvas(s, user, [_node("c1", "狐妖")])
            nothing_final = await _status(
                router.save_to_library(
                    canvas.id, router.SaveToLibrary(character="狐妖"), session=s, user=user
                )
            )
            missing = await _status(
                router.save_to_library(
                    canvas.id, router.SaveToLibrary(character="路人"), session=s, user=user
                )
            )
            base = await _artifact(s, canvas, "base", "c1")
            winter = await _artifact(s, canvas, "winter", "c2")
            canvas.graph = {
                "nodes": [
                    _node("c1", "狐妖", selected=base.id),
                    _node("c2", "狐妖", look="冬装", selected=winter.id),
                    _node("c3", "狐妖", look="夏装"),  # 没定稿的造型跳过
                ],
                "edges": [],
            }
            await s.flush()
            first = await router.save_to_library(
                canvas.id, router.SaveToLibrary(character="狐妖"), session=s, user=user
            )
            canvas.graph = {
                **canvas.graph,
                "nodes": [
                    {
                        **canvas.graph["nodes"][0],
                        "data": {**canvas.graph["nodes"][0]["data"], "prompt": "更新"},
                    },
                    *canvas.graph["nodes"][1:],
                ],
            }
            await s.flush()
            second = await router.save_to_library(
                canvas.id, router.SaveToLibrary(character="狐妖"), session=s, user=user
            )
            listed = await router.list_characters(canvas_id=None, session=s, user=user)
            # 存库的来源画布：库角色视为已在本项目，不能再加入一份
            here = await router.list_characters(canvas_id=canvas.id, session=s, user=user)
            again = await _status(
                router.add_from_library(
                    canvas.id,
                    router.FromLibrary(asset_id=second["id"], base_revision=canvas.revision),
                    session=s,
                    user=user,
                )
            )
            assert [item["in_project"] for item in here] == [True]
            assert again == (422, "这个角色已在本项目")
            foreign = await _status(
                router.save_to_library(
                    canvas.id, router.SaveToLibrary(character="狐妖"), session=s, user=other
                )
            )
            return nothing_final, missing, first, second, listed, foreign, base.id, winter.id

    nothing_final, missing, first, second, listed, foreign, base_id, winter_id = asyncio.run(_run())
    assert nothing_final == (422, "先给角色定稿")
    assert missing[0] == 422
    assert [look["name"] for look in first["looks"]] == ["默认", "冬装"]
    assert [look["artifact_id"] for look in first["looks"]] == [base_id, winter_id]
    assert first["description"] == "狐妖的外貌"
    # 同一画布再次保存：新版本取代旧版本，库里只有一条
    assert second["id"] != first["id"] and second["description"] == "更新"
    assert [item["id"] for item in listed] == [second["id"]]
    assert foreign[0] == 404


@requires_pg
def test_add_from_library_copies_artifacts_and_appends_nodes(pg):
    from app.db.models import Artifact
    from app.routers import character_library as router

    async def _run():
        async with pg() as s:
            user = await _user(s, "u")
            other = await _user(s, "other")
            source = await _canvas(s, user, [])
            a = await _artifact(s, source, "a", "c1")
            b = await _artifact(s, source, "b", "c2")
            source.graph = {
                "nodes": [
                    _node("c1", "狐妖", selected=a.id),
                    _node("c2", "狐妖", look="冬装", selected=b.id),
                ],
                "edges": [],
            }
            await s.flush()
            card = await router.save_to_library(
                source.id, router.SaveToLibrary(character="狐妖"), session=s, user=user
            )

            target = await _canvas(s, user, [_node("old", "书生")])
            before = await router.list_characters(canvas_id=target.id, session=s, user=user)
            stale = await _status(
                router.add_from_library(
                    target.id,
                    router.FromLibrary(asset_id=card["id"], base_revision=target.revision - 1),
                    session=s,
                    user=user,
                )
            )
            done = await router.add_from_library(
                target.id,
                router.FromLibrary(asset_id=card["id"], base_revision=target.revision),
                session=s,
                user=user,
            )
            again = await _status(
                router.add_from_library(
                    target.id,
                    router.FromLibrary(asset_id=card["id"], base_revision=target.revision),
                    session=s,
                    user=user,
                )
            )
            after = await router.list_characters(canvas_id=target.id, session=s, user=user)
            copies = list(
                (
                    await s.execute(
                        __import__("sqlalchemy")
                        .select(Artifact)
                        .where(Artifact.project_id == target.project_id)
                    )
                ).scalars()
            )
            foreign_card = await _status(
                router.add_from_library(
                    target.id,
                    router.FromLibrary(asset_id=card["id"], base_revision=target.revision),
                    session=s,
                    user=other,
                )
            )
            other_canvas = await _canvas(s, other, [])
            foreign_asset = await _status(
                router.add_from_library(
                    other_canvas.id,
                    router.FromLibrary(asset_id=card["id"], base_revision=other_canvas.revision),
                    session=s,
                    user=other,
                )
            )
            foreign_list = await _status(router.list_characters(canvas_id=target.id, session=s, user=other))
            return (
                before,
                stale,
                done,
                again,
                after,
                target,
                copies,
                foreign_card,
                foreign_asset,
                foreign_list,
                (a.id, b.id),
            )

    before, stale, done, again, after, target, copies, foreign_card, foreign_asset, foreign_list, sources = (
        asyncio.run(_run())
    )
    assert before[0]["in_project"] is False and after[0]["in_project"] is True
    assert stale[0] == 409
    assert again == (422, "这个角色已在本项目")
    assert done["revision"] == target.revision and len(done["node_ids"]) == 2
    added = [n for n in target.graph["nodes"] if n["id"] in done["node_ids"]]
    assert [(n["data"]["character"], n["data"]["look"]) for n in added] == [("狐妖", ""), ("狐妖", "冬装")]
    assert all(
        n["data"]["role"] == "character" and n["data"]["library_asset_id"] == after[0]["id"] for n in added
    )
    assert all(n["data"]["prompt"] == "狐妖的外貌" for n in added)
    # 新节点排在已有角色节点下方
    assert (
        min(n["position"]["y"] for n in added)
        > [n for n in target.graph["nodes"] if n["id"] == "old"][0]["position"]["y"]
    )
    # 每个造型在本项目复制了一行 Artifact，节点的 selected 指向它，且不是源画布的那行
    assert len(copies) == 2
    by_id = {c.id: c for c in copies}
    for node in added:
        copy = by_id[node["data"]["selected"]]
        assert (
            copy.artifact_metadata["canvas_node_id"] == node["id"]
            and copy.artifact_metadata["source"] == "canvas_upload"
        )
        assert copy.id not in sources and copy.width == 512
    assert foreign_card[0] == 404 and foreign_asset[0] == 404 and foreign_list[0] == 404


@requires_pg
def test_endpoints_registered_and_canvas_owner_only(pg):
    from app.main import app

    paths = {route.path for route in app.routes}
    assert "/api/library/characters" in paths
    assert "/api/library/characters/{asset_id}" in paths
    assert "/api/workspace/projects/{canvas_id}/characters/save-to-library" in paths
    assert "/api/workspace/projects/{canvas_id}/characters/from-library" in paths


def test_character_recognition_helpers():
    graph = {
        "nodes": [
            {
                "id": "a",
                "type": "image",
                "position": {"x": 0, "y": 0},
                "data": {"label": "角色 · 狐妖", "role": "character"},
            },
            {"id": "b", "type": "image", "position": {"x": 0, "y": 0}, "data": {"label": "角色 · 书生"}},
            {"id": "c", "type": "image", "position": {"x": 0, "y": 0}, "data": {"label": "镜头"}},
            {
                "id": "d",
                "type": "image",
                "position": {"x": 0, "y": 0},
                "data": {"character": "狐妖", "look": "冬装", "role": "character"},
            },
            {"id": "e", "type": "image", "position": {"x": 0, "y": 0}, "data": {"label": "孤立图"}},
        ],
        "edges": [
            {"id": "1", "source": "b", "target": "c", "targetHandle": "reference"},
            {"id": "2", "source": "c", "target": "x", "targetHandle": "first_frame"},
        ],
    }
    groups = service.character_groups(graph)
    assert {name: [n["id"] for n in nodes] for name, nodes in groups.items()} == {
        "狐妖": ["a", "d"],
        "书生": ["b"],
    }
    assert service.base_node(groups["狐妖"])["id"] == "a"
    stats = service.character_stats(graph, {"a": {"x1"}, "b": set()})
    assert stats == [{"name": "狐妖", "final": False}, {"name": "书生", "final": False}]


@requires_pg
def test_save_requires_selected_image_to_be_the_nodes_own_result(pg):
    from app.routers import character_library as router

    async def _run():
        async with pg() as s:
            user = await _user(s, "u")
            canvas = await _canvas(s, user, [])
            other_node = await _artifact(s, canvas, "other", "somebody-else")  # 别的节点的图
            loose = await _artifact(s, canvas, "loose")  # 项目里的图，但不属于任何节点
            own_winter = await _artifact(s, canvas, "winter", "c2")
            canvas.graph = {"nodes": [_node("c1", "狐妖", selected=other_node.id)], "edges": []}
            await s.flush()
            wrong_base = await _status(
                router.save_to_library(
                    canvas.id, router.SaveToLibrary(character="狐妖"), session=s, user=user
                )
            )
            canvas.graph = {"nodes": [_node("c1", "狐妖", selected=loose.id)], "edges": []}
            await s.flush()
            loose_base = await _status(
                router.save_to_library(
                    canvas.id, router.SaveToLibrary(character="狐妖"), session=s, user=user
                )
            )
            # 基础造型没定稿，只有冬装定稿：也不行
            canvas.graph = {
                "nodes": [_node("c1", "狐妖"), _node("c2", "狐妖", look="冬装", selected=own_winter.id)],
                "edges": [],
            }
            await s.flush()
            base_missing = await _status(
                router.save_to_library(
                    canvas.id, router.SaveToLibrary(character="狐妖"), session=s, user=user
                )
            )
            # 基础造型有效，冬装用了别的节点的图：冬装被跳过
            own_base = await _artifact(s, canvas, "base", "c1")
            canvas.graph = {
                "nodes": [
                    _node("c1", "狐妖", selected=own_base.id),
                    _node("c2", "狐妖", look="冬装", selected=other_node.id),
                ],
                "edges": [],
            }
            await s.flush()
            partial = await router.save_to_library(
                canvas.id, router.SaveToLibrary(character="狐妖"), session=s, user=user
            )
            return wrong_base, loose_base, base_missing, partial

    wrong_base, loose_base, base_missing, partial = asyncio.run(_run())
    assert wrong_base == loose_base == base_missing == (422, "先给角色定稿")
    assert [look["name"] for look in partial["looks"]] == ["默认"]


@requires_pg
def test_library_identity_survives_new_versions(pg):
    from app.routers import character_library as router

    async def _run():
        async with pg() as s:
            user = await _user(s, "u")
            source = await _canvas(s, user, [])
            a = await _artifact(s, source, "a", "c1")
            source.graph = {"nodes": [_node("c1", "狐妖", selected=a.id)], "edges": []}
            await s.flush()
            v1 = await router.save_to_library(
                source.id, router.SaveToLibrary(character="狐妖"), session=s, user=user
            )
            target = await _canvas(s, user, [])
            await router.add_from_library(
                target.id,
                router.FromLibrary(asset_id=v1["id"], base_revision=target.revision),
                session=s,
                user=user,
            )
            # 来源项目重新存入：产生新版本（新 id），身份不变
            v2 = await router.save_to_library(
                source.id, router.SaveToLibrary(character="狐妖"), session=s, user=user
            )
            listed = await router.list_characters(canvas_id=target.id, session=s, user=user)
            again = await _status(
                router.add_from_library(
                    target.id,
                    router.FromLibrary(asset_id=v2["id"], base_revision=target.revision),
                    session=s,
                    user=user,
                )
            )
            return v1, v2, listed, again, target

    v1, v2, listed, again, target = asyncio.run(_run())
    assert v2["id"] != v1["id"] and v2["root_id"] == v1["root_id"] == v1["id"]
    assert [(item["id"], item["in_project"]) for item in listed] == [(v2["id"], True)]
    assert again == (422, "这个角色已在本项目")
    assert [n["data"]["library_asset_id"] for n in target.graph["nodes"]] == [v1["id"]]


@requires_pg
def test_concurrent_add_from_library_only_one_wins(pg):
    """两个会话拿着同一个 revision 同时加入：行锁保证只有一个成功，另一个 409，不会互相覆盖。"""
    from app.db.models import NodeCanvas

    async def _run():
        async with pg() as setup:
            user = await _user(setup, "u")
            source = await _canvas(setup, user, [])
            a = await _artifact(setup, source, "a", "c1")
            source.graph = {"nodes": [_node("c1", "狐妖", selected=a.id)], "edges": []}
            await setup.flush()
            card = await service.save_to_library(setup, user, source, "狐妖")
            b = await _artifact(setup, source, "b", "c1")
            source.graph = {"nodes": [_node("c1", "书生", selected=b.id)], "edges": []}
            await setup.flush()
            other = await service.save_to_library(setup, user, source, "书生")
            target = await _canvas(setup, user, [])
            await setup.commit()
            ids = (user.id, target.id, target.revision, card["id"], other["id"])

        async def attempt(asset_id):
            async with pg() as s:
                from app.db.models import User

                me = await s.get(User, ids[0])
                canvas = await s.get(NodeCanvas, ids[1])
                try:
                    await service.add_from_library(s, me, canvas, asset_id, ids[2])
                    await s.commit()
                    return "ok"
                except canvas_service.CanvasConflict:
                    await s.rollback()
                    return "conflict"

        results = await asyncio.gather(attempt(ids[3]), attempt(ids[4]))
        async with pg() as s:
            final = await s.get(NodeCanvas, ids[1])
            return results, len(final.graph["nodes"]), final.revision

    results, node_count, revision = asyncio.run(_run())
    assert sorted(results) == ["conflict", "ok"]
    assert node_count == 1 and revision == 2
