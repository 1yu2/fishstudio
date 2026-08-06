"""系列剧（series）领域 Pydantic schema：分镜 payload 校验 + API 请求/响应模型。

分镜 JSON 结构对齐 docs/series-video-optimization-plan.md 5.1 节。
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ─────────────────────────────────────────
# 分镜 payload（文档 5.1）
# ─────────────────────────────────────────


def _check_time_range(start_ms: int, end_ms: int, label: str) -> None:
    if end_ms <= start_ms:
        raise ValueError(f"{label} 的 end_ms 必须大于 start_ms")


class DialogueTrack(BaseModel):
    speaker_asset_id: Optional[str] = None
    text: str
    lang: str = "zh"
    start_ms: int = 0
    end_ms: int = 0

    @model_validator(mode="after")
    def _check_range(self) -> "DialogueTrack":
        _check_time_range(self.start_ms, self.end_ms, "对白")
        return self


class SubtitleTrack(BaseModel):
    text: str
    lang: str = "zh"
    start_ms: int = 0
    end_ms: int = 0

    @model_validator(mode="after")
    def _check_range(self) -> "SubtitleTrack":
        _check_time_range(self.start_ms, self.end_ms, "字幕")
        return self


class CameraSpec(BaseModel):
    type: str = "fixed"
    description: str = ""


class TransitionSpec(BaseModel):
    type: str = "cut"


class ShotSpec(BaseModel):
    shot_id: Optional[str] = None
    number: int
    duration_ms: int = 5000
    visual_prompt: str = ""
    camera: CameraSpec = Field(default_factory=CameraSpec)
    transition: TransitionSpec = Field(default_factory=TransitionSpec)
    aspect_ratio: str = "9:16"
    character_asset_ids: list[str] = Field(default_factory=list)
    dialogue_tracks: list[DialogueTrack] = Field(default_factory=list)
    narration_tracks: list[DialogueTrack] = Field(default_factory=list)
    subtitle_tracks: list[SubtitleTrack] = Field(default_factory=list)

    @field_validator("duration_ms")
    @classmethod
    def _check_duration(cls, v: int) -> int:
        if v <= 0 or v > 60000:
            raise ValueError("镜头时长必须在 1~60000ms 之间")
        return v


class SceneSpec(BaseModel):
    scene_id: Optional[str] = None
    number: int
    location: str = ""
    time_of_day: str = ""
    description: str = ""
    shots: list[ShotSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_shot_numbers(self) -> "SceneSpec":
        nums = [s.number for s in self.shots]
        if len(nums) != len(set(nums)):
            raise ValueError("同一场景内镜头 number 必须唯一")
        return self


class EpisodeSpec(BaseModel):
    episode_id: Optional[str] = None
    number: int
    title: str = ""
    scenes: list[SceneSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_scene_numbers(self) -> "EpisodeSpec":
        nums = [s.number for s in self.scenes]
        if len(nums) != len(set(nums)):
            raise ValueError("同一集内场景 number 必须唯一")
        return self


class StoryboardPayload(BaseModel):
    schema_version: int = 1
    series_id: str = ""
    episodes: list[EpisodeSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_episode_numbers(self) -> "StoryboardPayload":
        nums = [e.number for e in self.episodes]
        if len(nums) != len(set(nums)):
            raise ValueError("集 number 必须唯一")
        return self


# ─────────────────────────────────────────
# API 请求/响应模型
# ─────────────────────────────────────────


class ProjectCreate(BaseModel):
    canvas_id: Optional[str] = None
    title: str = ""
    project_type: str = "short_drama"
    workflow_template_id: Optional[str] = None
    active_skill_ids: list[str] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    canvas_id: Optional[str] = None
    user_id: Optional[int] = None
    project_type: str
    workflow_template_id: Optional[str] = None
    title: str
    status: str
    active_skill_ids: list[Any]
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class EpisodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    episode_no: int
    title: str
    synopsis: str
    status: str
    created_at: datetime


class SceneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    episode_id: str
    scene_no: int
    location: str
    time_of_day: str
    description: str
    sort_order: int


class ShotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scene_id: str
    storyboard_revision_id: Optional[str] = None
    shot_no: int
    duration_ms: int
    visual_prompt: str
    camera: dict[str, Any]
    transition: dict[str, Any]
    aspect_ratio: str
    status: str
    sort_order: int
    dialogue_tracks: list[Any]
    narration_tracks: list[Any]
    subtitle_tracks: list[Any]


class StoryboardRevisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    episode_id: str
    revision: int
    schema_version: int
    payload: dict[str, Any]
    status: str
    created_by: str
    created_at: datetime


class AssetCardCreate(BaseModel):
    type: str
    name: str
    description: str = ""
    provider_meta: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    scope: str = "project"
    source: str = ""


class AssetCardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: Optional[str] = None  # scope=personal 的个人资产为 NULL（P2-3）
    user_id: Optional[int] = None
    type: str
    name: str
    version: int
    description: str
    provider_meta: dict[str, Any]
    scope: str
    tags: list[Any]
    source: str
    license_meta: dict[str, Any]
    status: str
    created_at: datetime


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    target_type: str
    target_id: str
    target_revision: int
    status: str
    approved_by: Optional[int] = None
    approved_at: Optional[datetime] = None
    comment: str
    created_at: datetime


def to_dict(model: BaseModel) -> dict[str, Any]:
    """ORM → dict 通用 helper：orm_row 先经 *Out 模型转换再 dump。"""
    return model.model_dump(mode="json")
