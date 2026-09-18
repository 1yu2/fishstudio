"""Typed Node 注册表：内置短剧模板的节点类型与 DAG seed。

核心原则（docs/series-video-optimization-plan.md S4A）：
- Workflow Step 是业务编排，GenerationJob 是收费执行；一个 Step 可创建多个 Job。
- Agent 和模板只能组合这里注册的 Typed Node；未知 node_type 一律拒绝。
- creates_jobs=True 的节点在 fan-out 前必须停在 waiting_approval 做费用确认。
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NodeTypeSpec:
    node_type: str
    description: str
    # 节点完成/推进前是否需要人工审批（审批未过则停在 waiting_approval）
    requires_approval: bool = False
    # 节点是否会 fan-out 创建 GenerationJob（收费执行）
    creates_jobs: bool = False
    # 节点是否产生外部费用（review 0.6/P1-4）：True 时必须先过 Cost Gate
    # （run.input_snapshot.cost_confirmed + quote 校验通过）才能展开 job；
    # 本地 ffmpeg 等免费节点（episode.render）为 False
    requires_cost_confirm: bool = False
    # 节点作用域：project（每 run 一个）或 episode（每集一个）
    scope: str = "episode"
    # 参数白名单 schema 描述（仅文档化用途，MVP 不强校验类型）
    allowed_params: dict[str, Any] = field(default_factory=dict)


NODE_TYPES: dict[str, NodeTypeSpec] = {
    "project.initialize": NodeTypeSpec(
        node_type="project.initialize",
        description="初始化项目运行上下文（集数、画幅、策略），自动完成",
        scope="project",
        allowed_params={"episodes": "int", "aspect_ratio": "str"},
    ),
    "outline.generate": NodeTypeSpec(
        node_type="outline.generate",
        description="生成故事大纲（LLM 创意节点，MVP 中由 Agent/用户产出后标记完成）",
        scope="project",
        allowed_params={"goal": "str"},
    ),
    "series.bible.generate": NodeTypeSpec(
        node_type="series.bible.generate",
        description="生成并保存不可变 Series Bible revision",
        scope="project",
        allowed_params={"prompt_version": "str"},
    ),
    "series.bible.approve": NodeTypeSpec(
        node_type="series.bible.approve",
        description="Series Bible 人工审批 Gate",
        requires_approval=True,
        scope="project",
    ),
    "style.bible.generate": NodeTypeSpec(
        node_type="style.bible.generate",
        description="生成并保存不可变 Style Bible revision",
        scope="project",
        allowed_params={"prompt_version": "str"},
    ),
    "style.bible.approve": NodeTypeSpec(
        node_type="style.bible.approve",
        description="Style Bible 人工审批 Gate",
        requires_approval=True,
        scope="project",
    ),
    "episode.script.generate": NodeTypeSpec(
        node_type="episode.script.generate",
        description="生成并保存结构化单集剧本 revision（由 Agent/Studio 产出）",
        allowed_params={"episode_id": "str", "prompt_version": "str"},
    ),
    "episode.script.approve": NodeTypeSpec(
        node_type="episode.script.approve",
        description="剧本审稿 Gate：当前 ScriptRevision approved 后才进入导演分镜",
        requires_approval=True,
        allowed_params={"episode_id": "str", "revision": "int"},
    ),
    "episode.script.review": NodeTypeSpec(
        node_type="episode.script.review",
        description="Observer 结构化剧本复审；严重问题阻止自动晋级",
        allowed_params={"episode_id": "str", "revision": "int"},
    ),
    "director.plan.generate": NodeTypeSpec(
        node_type="director.plan.generate",
        description="生成并保存不可变 DirectorPlan revision",
        allowed_params={"episode_id": "str", "prompt_version": "str"},
    ),
    "director.plan.approve": NodeTypeSpec(
        node_type="director.plan.approve",
        description="DirectorPlan 人工审批 Gate",
        requires_approval=True,
        allowed_params={"episode_id": "str", "revision": "int"},
    ),
    "storyboard.generate": NodeTypeSpec(
        node_type="storyboard.generate",
        description="生成/保存一集分镜（由 save_storyboard 工具产出后标记完成）",
        allowed_params={"episode_id": "str"},
    ),
    "storyboard.approve": NodeTypeSpec(
        node_type="storyboard.approve",
        description="分镜审批 Gate：当前 revision 被 approved 才推进",
        requires_approval=True,
        allowed_params={"episode_id": "str", "revision": "int"},
    ),
    "storyboard.continuity.check": NodeTypeSpec(
        node_type="storyboard.continuity.check",
        description="Observer 分镜连续性检查；问题定位到镜头",
        allowed_params={"episode_id": "str", "revision": "int"},
    ),
    "asset.character.generate": NodeTypeSpec(
        node_type="asset.character.generate",
        description="生成/登记角色资产（MVP 由 create_character_asset 工具产出）",
        allowed_params={"characters": "list"},
    ),
    "asset.version.approve": NodeTypeSpec(
        node_type="asset.version.approve",
        description="角色资产版本审批 Gate：项目全部角色资产当前版本 approved 才推进",
        requires_approval=True,
    ),
    "shot.image.generate": NodeTypeSpec(
        node_type="shot.image.generate",
        description="批量生成镜头首帧图（fan-out GenerationJob，收费 Cost Gate 控制）",
        creates_jobs=True,
        requires_cost_confirm=True,
        allowed_params={"resolution": "str", "candidate_count": "int"},
    ),
    "shot.video.generate": NodeTypeSpec(
        node_type="shot.video.generate",
        description="批量生成镜头视频（收费 fan-out，费用确认前停 waiting_approval）",
        requires_approval=True,  # 费用确认 Gate（cost_confirmed + generation gate）
        creates_jobs=True,
        requires_cost_confirm=True,
        allowed_params={"resolution": "str", "duration_ms": "int", "mode": "str"},
    ),
    "shot.audio.generate": NodeTypeSpec(
        node_type="shot.audio.generate",
        description="批量生成镜头对白/旁白语音（TTS fan-out，产物回填 track.audio_storage_key）",
        creates_jobs=True,
        requires_cost_confirm=True,
        allowed_params={"lang": "str"},
    ),
    "timeline.plan": NodeTypeSpec(
        node_type="timeline.plan",
        description="从正式候选创建不可变 TimelineRevision / EDL，并等待人工审批",
        allowed_params={"export_profile": "str"},
    ),
    "episode.render": NodeTypeSpec(
        node_type="episode.render",
        description="整集合成渲染（单个 render_episode Job）",
        creates_jobs=True,
        requires_approval=True,
        allowed_params={"resolution": "str"},
    ),
    "episode.director.review": NodeTypeSpec(
        node_type="episode.director.review",
        description="导演终审预览与语义 QC，失败时返回最小可修复节点",
    ),
    "episode.render.final": NodeTypeSpec(
        node_type="episode.render.final",
        description="严格消费已审批 Timeline 的 final profile 渲染",
        creates_jobs=True,
        requires_approval=True,
        allowed_params={"resolution": "str", "export_profile": "str"},
    ),
    "episode.final.qc": NodeTypeSpec(
        node_type="episode.final.qc",
        description="最终技术与语义质量检查",
    ),
    "publish.package.generate": NodeTypeSpec(
        node_type="publish.package.generate",
        description="生成只供人工发布的成片、封面、文案与版本 manifest",
    ),
}

# 内置短剧模板 DAG seed（v3：加入 Series/Style Bible 与审批 Gate）。
#   project.initialize → series.bible.generate → series.bible.approve ─┐
#                      → style.bible.generate  → style.bible.approve  ─┴→ outline.generate
#   outline.generate → episode.script.generate
#     ├→ episode.script.approve → storyboard.generate → storyboard.approve ─┐
#     └→ asset.character.generate → asset.version.approve ─────────────────┴→ shot.image.generate
#         → shot.video.generate → shot.audio.generate → episode.render
# 模板以 version=2 新建；存量 v1 Run/Step 不变，新 Run 使用剧本 Gate。
SHORT_DRAMA_TEMPLATE_GRAPH: dict[str, Any] = {
    "schema_version": 1,
    "nodes": [
        {"node_key": "project.initialize", "node_type": "project.initialize"},
        {"node_key": "series.bible.generate", "node_type": "series.bible.generate"},
        {"node_key": "series.bible.approve", "node_type": "series.bible.approve"},
        {"node_key": "style.bible.generate", "node_type": "style.bible.generate"},
        {"node_key": "style.bible.approve", "node_type": "style.bible.approve"},
        {"node_key": "outline.generate", "node_type": "outline.generate"},
        {"node_key": "episode.script.generate", "node_type": "episode.script.generate"},
        {"node_key": "episode.script.review", "node_type": "episode.script.review"},
        {"node_key": "episode.script.approve", "node_type": "episode.script.approve"},
        {"node_key": "director.plan.generate", "node_type": "director.plan.generate"},
        {"node_key": "director.plan.approve", "node_type": "director.plan.approve"},
        {"node_key": "storyboard.generate", "node_type": "storyboard.generate"},
        {"node_key": "storyboard.continuity.check", "node_type": "storyboard.continuity.check"},
        {"node_key": "storyboard.approve", "node_type": "storyboard.approve"},
        {"node_key": "asset.character.generate", "node_type": "asset.character.generate"},
        {"node_key": "asset.version.approve", "node_type": "asset.version.approve"},
        {"node_key": "shot.image.generate", "node_type": "shot.image.generate"},
        {"node_key": "shot.video.generate", "node_type": "shot.video.generate"},
        {"node_key": "shot.audio.generate", "node_type": "shot.audio.generate"},
        {"node_key": "timeline.plan", "node_type": "timeline.plan"},
        {"node_key": "episode.director.review", "node_type": "episode.director.review"},
        {"node_key": "episode.render", "node_type": "episode.render.final"},
        {"node_key": "episode.final.qc", "node_type": "episode.final.qc"},
        {"node_key": "publish.package.generate", "node_type": "publish.package.generate"},
    ],
    "edges": [
        ["project.initialize", "series.bible.generate"],
        ["series.bible.generate", "series.bible.approve"],
        ["project.initialize", "style.bible.generate"],
        ["style.bible.generate", "style.bible.approve"],
        ["series.bible.approve", "outline.generate"],
        ["style.bible.approve", "outline.generate"],
        ["outline.generate", "episode.script.generate"],
        ["episode.script.generate", "episode.script.review"],
        ["episode.script.review", "episode.script.approve"],
        ["episode.script.approve", "director.plan.generate"],
        ["director.plan.generate", "director.plan.approve"],
        ["director.plan.approve", "storyboard.generate"],
        ["storyboard.generate", "storyboard.continuity.check"],
        ["storyboard.continuity.check", "storyboard.approve"],
        ["outline.generate", "asset.character.generate"],
        ["asset.character.generate", "asset.version.approve"],
        ["storyboard.approve", "shot.image.generate"],
        ["asset.version.approve", "shot.image.generate"],
        ["shot.image.generate", "shot.video.generate"],
        ["shot.video.generate", "shot.audio.generate"],
        ["shot.audio.generate", "timeline.plan"],
        ["timeline.plan", "episode.director.review"],
        ["episode.director.review", "episode.render"],
        ["episode.render", "episode.final.qc"],
        ["episode.final.qc", "publish.package.generate"],
    ],
}

SHORT_DRAMA_TEMPLATE_DEFAULT_POLICY: dict[str, Any] = {
    "provider_policy": "balanced",
    "resolution": "768P",
    "max_shots_per_episode": 16,
    "candidate_count": 1,
}


def get_node_spec(node_type: str) -> NodeTypeSpec:
    """查节点类型；未注册一律拒绝。"""
    spec = NODE_TYPES.get(node_type)
    if spec is None:
        raise ValueError(f"unknown node_type: {node_type}")
    return spec


def toposort(node_keys: list[str], edges: list[list[str]]) -> list[str]:
    """Kahn 拓扑排序；有环抛 ValueError。纯函数，供 validate_plan / 测试复用。"""
    indeg = {k: 0 for k in node_keys}
    for a, b in edges:
        if a not in indeg or b not in indeg:
            raise ValueError(f"edge references unknown node: {a} -> {b}")
        indeg[b] += 1
    queue = [k for k in node_keys if indeg[k] == 0]
    order: list[str] = []
    # 邻接表
    adj: dict[str, list[str]] = {k: [] for k in node_keys}
    for a, b in edges:
        adj[a].append(b)
    while queue:
        k = queue.pop(0)
        order.append(k)
        for nxt in adj[k]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    if len(order) != len(node_keys):
        raise ValueError("graph has a cycle")
    return order
