"""EDL 直接渲染：模型校验、无 Shot 渲染与真实 PG 产物建档。"""

import asyncio
import json
import os
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_retry_integration import pg  # 复用专用测试库的建库、迁移和清理流程

from app.db.models import Artifact, Episode, SeriesProject
from app.services import asset_service, timeline_service
from app.services import timeline_renderer as tr


def _spec(ids):
    clips = []
    cursor = 0
    for index, aid in enumerate(ids):
        speed = (1, 2, 0.5)[index]
        clips.append(
            dict(
                id=f"clip-{index}",
                artifact_id=aid,
                artifact_hash=f"hash-{index}",
                source_in_ms=250,
                source_out_ms=1250,
                timeline_start_ms=cursor,
                speed=speed,
            )
        )
        cursor += round(1000 / speed)
    return {"aspect_ratio": "1:1", "video_tracks": [{"clips": clips}]}


@pytest.fixture
def media(tmp_path, monkeypatch):
    from app.services.storage_backend import storage

    monkeypatch.setattr(storage, "backend", "local")
    monkeypatch.setattr(asset_service, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(tr, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(tr, "VIDEOS_DIR", tmp_path / "videos")
    artifacts = []
    for index, size in enumerate(("320x180", "180x320", "240x240")):
        key = f"input-{index}.mp4"
        tr._run_ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size={size}:rate=24:duration=1.5",
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(tmp_path / key),
            ]
        )
        artifacts.append(
            Artifact(
                id=f"artifact-{index}",
                project_id="project",
                type="video",
                storage_key=key,
                content_hash=f"hash-{index}",
            )
        )
    return tmp_path, artifacts


def _session(artifacts):
    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: artifacts))
    session.get.side_effect = lambda model, aid: next((a for a in artifacts if a.id == aid), None)
    return session


@pytest.mark.parametrize(
    "spec",
    [
        {},
        {"video_tracks": [{"clips": [{"id": "bad", "artifact_id": "a", "source_out_ms": 0}]}]},
        {"audio_tracks": [{"clips": [{"id": "bad", "kind": "invalid"}]}]},
    ],
)
async def test_invalid_edl_before_database(spec):
    with pytest.raises(ValueError):
        await tr.render_timeline(None, project_id="p", spec=spec)


@pytest.mark.parametrize("group", ["video_tracks", "audio_tracks", "overlay_tracks"])
@pytest.mark.parametrize("failure", ["foreign", "hash"])
async def test_artifact_validation(group, failure):
    artifact = Artifact(id="a", project_id="other" if failure == "foreign" else "p", content_hash="actual")
    spec = _spec(["a"])
    spec["video_tracks"][0]["clips"][0]["artifact_hash"] = ""
    if group == "video_tracks":
        spec[group][0]["clips"][0]["artifact_hash"] = "wrong"
    else:
        spec[group] = [
            {
                "clips": [
                    {
                        "id": "extra",
                        "artifact_id": "a",
                        "artifact_hash": "wrong",
                        "kind": "bgm",
                        "timeline_start_ms": 0,
                        "start_ms": 0,
                        "end_ms": 500,
                    }
                ]
            }
        ]
    with pytest.raises(ValueError, match="another project|hash changed"):
        await tr.render_timeline(_session([artifact]), project_id="p", spec=spec)


@pytest.mark.parametrize("aspect, expected", [(None, "1:1"), ("16:9", "16:9"), (None, "9:16")])
async def test_render_without_shots(media, monkeypatch, aspect, expected):
    root, artifacts = media
    create = AsyncMock(side_effect=lambda session, **kw: SimpleNamespace(id="output", **kw))
    monkeypatch.setattr(asset_service, "create_artifact", create)
    load = AsyncMock(side_effect=AssertionError("直接 EDL 不应查询 Shot"))
    monkeypatch.setattr(tr, "_load_shots", load)
    spec = _spec([a.id for a in artifacts])
    if expected == "9:16":
        spec.pop("aspect_ratio")
    result = await tr.render_timeline(
        _session(artifacts),
        project_id="project",
        spec=spec,
        output_aspect=aspect,
        burn_subtitles=False,
    )
    path = root / result["storage_key"]
    assert path.is_file()
    assert result["duration_ms"] == 3500
    assert result["manifest"]["source"] == "edl"
    assert result["manifest"]["output_aspect"] == expected
    assert result["manifest"]["episode_id"] is None
    load.assert_not_awaited()
    # 实际解码验证，环境没有 ffprobe 时仍覆盖完整 ffmpeg 渲染链路。
    import imageio_ffmpeg

    frames = imageio_ffmpeg.read_frames(str(path))
    metadata = next(frames)
    frames.close()
    assert abs(metadata["duration"] - 3.5) <= 0.1
    assert metadata["size"] == tr.ASPECT_SPECS[expected]
    assert metadata["codec"] == "h264"
    assert "yuv420p" in metadata["pix_fmt"]
    assert metadata["audio_codec"] == "aac"
    data = path.read_bytes()
    assert data.index(b"moov") < data.index(b"mdat")


async def test_episode_timeline_without_shots(media, monkeypatch):
    root, artifacts = media
    timeline = SimpleNamespace(
        spec=_spec([a.id for a in artifacts]), id="rev", revision=1, content_hash="hash"
    )
    monkeypatch.setattr(tr, "_load_shots", AsyncMock(return_value=[]))
    monkeypatch.setattr(timeline_service, "get_render_revision", AsyncMock(return_value=timeline))
    monkeypatch.setattr(
        asset_service,
        "create_artifact",
        AsyncMock(side_effect=lambda session, **kw: SimpleNamespace(id="output", **kw)),
    )
    result = await tr.render_episode(
        _session(artifacts),
        project_id="project",
        episode_id="e",
        timeline_revision_id="rev",
        render_mode="final",
    )
    assert (root / result["storage_key"]).is_file()
    assert result["manifest"]["timeline_revision_id"] == "rev"
    assert result["manifest"]["is_publishable"]
    assert result["duration_ms"] == 3500


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL 未设置，跳过真实 PG 渲染测试"
)
def test_render_timeline_pg(pg, media):
    root, artifacts = media

    async def run():
        async with pg() as session:
            project = SeriesProject(title="edl-render")
            session.add(project)
            await session.flush()
            episode = Episode(project_id=project.id, episode_no=1)
            session.add(episode)
            for artifact in artifacts:
                artifact.project_id = project.id
                session.add(artifact)
            await session.commit()
            result = await tr.render_timeline(
                session,
                project_id=project.id,
                episode_id=episode.id,
                spec=_spec([a.id for a in artifacts]),
                output_aspect="16:9",
            )
            output = await session.get(Artifact, result["artifact_id"])
            assert output.project_id == project.id
            assert output.artifact_metadata["episode_id"] == episode.id
            assert result["manifest"]["source"] == "edl"
            path = root / result["storage_key"]
            assert path.is_file()
            # 用 imageio-ffmpeg 读流信息，不依赖系统 ffprobe
            import imageio_ffmpeg

            frames = imageio_ffmpeg.read_frames(str(path))
            metadata = next(frames)
            frames.close()
            assert abs(metadata["duration"] * 1000 - 3500) <= 100
            assert metadata["codec"] == "h264" and "yuv420p" in metadata["pix_fmt"]
            assert metadata["size"] == (1280, 720)
            assert metadata["audio_codec"] == "aac"
            assert output.content_hash
            other = SeriesProject(title="other-project")
            session.add(other)
            await session.flush()
            spec = _spec([a.id for a in artifacts])
            with pytest.raises(ValueError, match="another project"):
                await tr.render_timeline(session, project_id=other.id, spec=spec)
            spec["video_tracks"][0]["clips"][0]["artifact_hash"] = "changed"
            with pytest.raises(ValueError, match="hash changed"):
                await tr.render_timeline(session, project_id=project.id, spec=spec)

    asyncio.run(run())
