from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.routers.series import StudioStartRequest
from app.services import series_service


def test_studio_start_request_bounds_episode_count_and_duration():
    request = StudioStartRequest(title="测试项目", episode_count=8, target_duration_ms=60_000)
    assert request.episode_count == 8
    assert request.target_duration_ms == 60_000

    with pytest.raises(ValidationError):
        StudioStartRequest(title="测试项目", episode_count=0)


@pytest.mark.asyncio
async def test_workspace_mode_switch_only_updates_settings(monkeypatch):
    project = SimpleNamespace(settings={"story": "原故事"})
    session = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())

    async def fake_get_project(*_args, **_kwargs):
        return project

    monkeypatch.setattr(series_service, "get_project", fake_get_project)
    result = await series_service.set_workspace_mode(
        session, "project-1", "professional", user_id=1
    )

    assert result is project
    assert project.settings == {"story": "原故事", "workspace_mode": "professional"}
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(project)


@pytest.mark.asyncio
async def test_workspace_mode_rejects_unknown_mode():
    with pytest.raises(ValueError, match="quick or professional"):
        await series_service.set_workspace_mode(SimpleNamespace(), "project-1", "legacy")
