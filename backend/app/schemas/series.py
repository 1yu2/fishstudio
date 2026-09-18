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
    director_intent_key: str = ""
    number: int
    duration_ms: int = 5000
    shot_type: str = ""
    narrative_purpose: str = ""
    composition: str = ""
    action_start: str = ""
    action_end: str = ""
    emotion: str = ""
    first_frame_prompt: str = ""
    last_frame_prompt: str = ""
    generation_mode: str = "text"
    continuity_constraints: dict[str, Any] = Field(default_factory=dict)
    risk_tags: list[str] = Field(default_factory=list)
    locked_asset_versions: dict[str, int] = Field(default_factory=dict)
    qa_status: str = "pending"
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
# 单集剧本 payload（不可变 ScriptRevision）
# ─────────────────────────────────────────


class ScriptDialogue(BaseModel):
    speaker: str
    text: str
    character_asset_id: Optional[str] = None
    emotion: str = ""


class ScriptScene(BaseModel):
    number: int
    slugline: str = ""
    location: str = ""
    time_of_day: str = ""
    objective: str = ""
    entry_state: dict[str, Any] = Field(default_factory=dict)
    action: list[str] = Field(default_factory=list)
    dialogue: list[ScriptDialogue] = Field(default_factory=list)
    narration: list[str] = Field(default_factory=list)
    exit_state: dict[str, Any] = Field(default_factory=dict)


class ScriptPayload(BaseModel):
    schema_version: int = 1
    episode_objective: str = Field(min_length=1)
    hook: str = Field(min_length=1)
    conflict: str = Field(min_length=1)
    payoff: str = ""
    cliffhanger: str = Field(min_length=1)
    scenes: list[ScriptScene] = Field(min_length=1)
    character_state_changes: dict[str, Any] = Field(default_factory=dict)
    carry_over: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_scene_numbers(self) -> "ScriptPayload":
        nums = [scene.number for scene in self.scenes]
        if len(nums) != len(set(nums)):
            raise ValueError("同一剧本内场景 number 必须唯一")
        return self


# ─────────────────────────────────────────
# 整季 Bible payload
# ─────────────────────────────────────────


class SeasonArcItem(BaseModel):
    episode: int = Field(ge=1)
    dramatic_goal: str = ""
    hook: str = ""
    cliffhanger: str = ""


class BibleCharacter(BaseModel):
    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    goal: str = ""
    flaw: str = ""
    arc: str = ""


class SeriesBiblePayload(BaseModel):
    schema_version: int = 1
    premise: str = Field(min_length=1)
    theme: str = Field(min_length=1)
    genre: str = ""
    audience: str = ""
    world_rules: list[str] = Field(default_factory=list)
    main_conflict: str = Field(min_length=1)
    characters: list[BibleCharacter] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    season_arc: list[SeasonArcItem] = Field(default_factory=list)
    forbidden_content: list[str] = Field(default_factory=list)
    continuity_rules: list[str] = Field(default_factory=list)


class StyleBiblePayload(BaseModel):
    schema_version: int = 1
    visual_style: str = Field(min_length=1)
    color_palette: list[str] = Field(default_factory=list)
    lighting: str = ""
    composition: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)
    aspect_ratio: str = "9:16"
    platform: str = "xiaohongshu"
    negative_constraints: list[str] = Field(default_factory=list)


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
    series_bible_revision_id: Optional[str] = None
    style_bible_revision_id: Optional[str] = None
    episode_no: int
    title: str
    synopsis: str
    objective: str
    hook: str
    conflict: str
    payoff: str
    cliffhanger: str
    target_duration_ms: int
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
    director_plan_revision_id: Optional[str] = None
    director_intent_key: str = ""
    shot_no: int
    duration_ms: int
    shot_type: str
    narrative_purpose: str
    composition: str
    action_start: str
    action_end: str
    emotion: str
    first_frame_prompt: str
    last_frame_prompt: str
    generation_mode: str
    continuity_constraints: dict[str, Any]
    risk_tags: list[Any]
    locked_asset_versions: dict[str, Any]
    qa_status: str
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
    based_on_script_revision_id: Optional[str] = None
    status: str
    created_by: str
    created_at: datetime


class ScriptRevisionCreate(BaseModel):
    structured_payload: dict[str, Any]
    source_type: str = "user"
    based_on_revision: Optional[int] = None
    schema_version: int = 1

    @field_validator("source_type")
    @classmethod
    def _check_source_type(cls, value: str) -> str:
        if value not in {"agent", "user", "import"}:
            raise ValueError("source_type 必须是 agent、user 或 import")
        return value


class ScriptRevisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    episode_id: str
    based_on_series_bible_revision_id: Optional[str] = None
    based_on_style_bible_revision_id: Optional[str] = None
    revision: int
    schema_version: int
    structured_payload: dict[str, Any]
    source_type: str
    based_on_revision: Optional[int] = None
    status: str
    created_by: str
    created_at: datetime


class BibleRevisionCreate(BaseModel):
    structured_payload: dict[str, Any]
    source_type: str = "user"
    based_on_revision: Optional[int] = None
    schema_version: int = 1

    @field_validator("source_type")
    @classmethod
    def _check_source_type(cls, value: str) -> str:
        if value not in {"agent", "user", "import"}:
            raise ValueError("source_type 必须是 agent、user 或 import")
        return value


class BibleRevisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    revision: int
    schema_version: int
    structured_payload: dict[str, Any]
    source_type: str
    based_on_revision: Optional[int] = None
    status: str
    created_by: str
    created_at: datetime


class DirectorIntent(BaseModel):
    key: str = Field(min_length=1)
    scene_number: int = Field(ge=1)
    beat: str = Field(min_length=1)
    objective: str = ""
    performance: str = ""
    camera_strategy: str = ""
    rhythm: str = ""
    continuity_requirements: dict[str, Any] = Field(default_factory=dict)


class DirectorPlanPayload(BaseModel):
    schema_version: int = 1
    creative_statement: str = Field(min_length=1)
    pacing_strategy: str = ""
    hook_strategy: str = ""
    visual_strategy: str = ""
    audio_strategy: str = ""
    intents: list[DirectorIntent] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_intents(self) -> "DirectorPlanPayload":
        keys = [item.key for item in self.intents]
        if len(keys) != len(set(keys)):
            raise ValueError("导演意图 key 必须唯一")
        return self


class DirectorPlanCreate(BaseModel):
    based_on_script_revision_id: str
    payload: dict[str, Any]
    asset_version_snapshot: dict[str, Any] = Field(default_factory=dict)
    platform_constraints: dict[str, Any] = Field(default_factory=dict)
    source_type: str = "agent"


class DirectorPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    episode_id: str
    revision: int
    based_on_script_revision_id: str
    series_bible_revision_id: Optional[str] = None
    style_bible_revision_id: Optional[str] = None
    asset_version_snapshot: dict[str, Any]
    platform_constraints: dict[str, Any]
    payload: dict[str, Any]
    source_type: str
    status: str
    created_by: str
    created_at: datetime


class ObserverFinding(BaseModel):
    code: str = Field(min_length=1)
    category: str = Field(min_length=1)
    severity: str
    evidence: str = ""
    location: dict[str, Any] = Field(default_factory=dict)
    suggested_fix: str = ""

    @field_validator("severity")
    @classmethod
    def _severity(cls, value: str) -> str:
        if value not in {"info", "warning", "major", "critical"}:
            raise ValueError("severity 必须是 info、warning、major 或 critical")
        return value


class ObserverReviewCreate(BaseModel):
    review_type: str
    target_type: str
    target_id: str
    target_revision: int = Field(ge=1)
    score: float = Field(ge=0, le=100)
    deterministic_findings: list[ObserverFinding] = Field(default_factory=list)
    specialist_findings: list[ObserverFinding] = Field(default_factory=list)
    llm_findings: list[ObserverFinding] = Field(default_factory=list)
    summary: str = ""
    model_invocation_id: Optional[str] = None
    review_scope: dict[str, Any] = Field(default_factory=dict)


class ObserverRunRequest(BaseModel):
    review_type: str
    target_type: str
    target_id: str
    target_revision: int = Field(ge=1)
    issue_codes: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    prompt_version: str = "observer_v1"


class ObserverReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    episode_id: str
    review_type: str
    target_type: str
    target_id: str
    target_revision: int
    score: float
    blocking: bool
    deterministic_findings: list[Any]
    specialist_findings: list[Any]
    llm_findings: list[Any]
    summary: str
    model_invocation_id: Optional[str] = None
    review_scope: dict[str, Any] = Field(default_factory=dict)
    model_cost_cny: Optional[float] = None
    created_by: str
    created_at: datetime


class CharacterProfile(BaseModel):
    stable_traits: list[str] = Field(default_factory=list)
    costume: dict[str, Any] = Field(default_factory=dict)
    expressions: list[str] = Field(default_factory=list)
    poses: list[str] = Field(default_factory=list)
    multi_angle_artifact_ids: list[str] = Field(default_factory=list)
    voice_asset_id: Optional[str] = None
    allowed_changes: list[str] = Field(default_factory=list)
    forbidden_changes: list[str] = Field(default_factory=list)


class SceneProfile(BaseModel):
    layout: str = ""
    time_of_day: str = ""
    weather: str = ""
    lighting: str = ""
    fixed_props: list[str] = Field(default_factory=list)
    multi_angle_artifact_ids: list[str] = Field(default_factory=list)


class StyleProfile(BaseModel):
    visual_rules: list[str] = Field(default_factory=list)
    palette: list[str] = Field(default_factory=list)
    negative_rules: list[str] = Field(default_factory=list)
    lora_id: Optional[str] = None


class VoiceProfile(BaseModel):
    voice_id: str = ""
    language: str = "zh"
    tone: str = ""
    emotion_range: list[str] = Field(default_factory=list)


class PropProfile(BaseModel):
    stable_traits: list[str] = Field(default_factory=list)
    scale: str = ""
    material: str = ""
    multi_angle_artifact_ids: list[str] = Field(default_factory=list)


ASSET_PROFILE_SCHEMAS = {
    "character": CharacterProfile,
    "scene": SceneProfile,
    "style": StyleProfile,
    "voice": VoiceProfile,
    "prop": PropProfile,
}


def validate_asset_profile(asset_type: str, profile: dict[str, Any]) -> dict[str, Any]:
    schema = ASSET_PROFILE_SCHEMAS.get(asset_type)
    if schema is None:
        raise ValueError("资产 type 必须是 character、scene、style、voice 或 prop")
    return schema.model_validate(profile).model_dump(mode="json")


class AssetCardCreate(BaseModel):
    type: str
    name: str
    description: str = ""
    provider_meta: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    scope: str = "project"
    source: str = ""
    profile_schema_version: int = 1
    profile: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_profile(self) -> "AssetCardCreate":
        self.profile = validate_asset_profile(self.type, self.profile)
        return self


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
    profile_schema_version: int = 1
    profile: dict[str, Any] = Field(default_factory=dict)
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
