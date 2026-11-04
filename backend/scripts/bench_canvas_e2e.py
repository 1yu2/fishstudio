#!/usr/bin/env python
"""画布全流程端到端基准脚本（阶段 1 退出标准：基准集 10 个用例端到端导出成片）。

只通过 HTTP API 驱动一个已启动的后端，模拟真实用户，不直接调用 service：
  新建画布 → Copilot 拆分镜头与台词 → 角色设定图（基准集自带的优先上传，没有才生成）
  → 批量生成关键帧 → 批量生成视频 → 批量生成配音（每一步先批量报价、确认预算后再运行）
  → 渲染成片 → 读取出片检查（qc）→ 下载成片。

用法（在 backend/ 下）：
  BENCH_USERNAME=... BENCH_PASSWORD=... python scripts/bench_canvas_e2e.py \\
      --base-url http://127.0.0.1:8000 --stories 10 --max-shots 6 --budget-yuan 50 --out-dir ./bench-results

环境变量（不会打印、不会写进报告）：
  BENCH_USERNAME / BENCH_PASSWORD   登录账号（未设置时回退 ADMIN_USERNAME / ADMIN_PASSWORD）

主要参数：
  --base-url URL         后端地址，默认 http://127.0.0.1:8000
  --stories N|key,key    取前 N 个故事（只考虑镜头数 >= --min-shots 的，默认 >= 5）或按 key 指定，默认 10
  --max-shots N          每个用例截断到前 N 个镜头，默认 6（花费几乎与它成正比）
  --budget-yuan X        总预算上限（元），真实跑必填。见下“预算保护”
  --dry-quote            只报价：不调用任何收费接口（含拆分、批量运行、单节点运行）。用基准集的情节文本在本地
                         直接拼出镜头，经保存画布接口建图后只调用批量报价；脚本在 HTTP 层强制拦截收费接口
  --resolution 480p      视频清晰度档。接口没有直接的分辨率参数，视频清晰度由模型路由按档位决定：
                         480p 对应 preview 档（Ark 的低分辨率档），其余对应 final 档。默认 480p
  --video-model / --image-model   指定 Deployment slug；不填走默认路由
  --sheets-dir DIR       角色设定图所在目录（基准集 reference_image 的相对根）。默认依次找 backend/storage
                         和各上级目录里的 backend/storage（设定图不入库，只存在于主仓库的 storage/ 下）
  --step-timeout S       单个批量步骤最长等待秒数，默认 900；--render-timeout 渲染，默认 300
  --settle-timeout S     异常 / 超时后取消批次并等在途任务进入终态的上限秒数，默认 300
  --out-dir DIR          报告目录，默认 ./bench-results，实际写到 <out-dir>/<时间戳>/
  --no-eval-run          不写 EvalRun（默认把每个用例写成一条 /api/eval/runs 记录）
  --allow-unpriced       允许运行“未配置价格”的收费节点（默认拒绝：价格未知的节点会绕过预算）
  --worker-max-attempts N  假设 worker 对每个任务最多尝试几次，默认 3；运行后会核对任务的 max_attempts
  --max-split-calls N    拆分接口总调用次数上限，默认等于用例数；每个用例只拆分一次，失败不重试
  --llm-input-price / --llm-output-price  拆分用对话模型的单价（元 / 百万 token），用于估算拆分花费，默认 4 / 16
  --allow-mock-media     仅用于 mock 联调：允许图片 / 视频 / 拆分来自 mock，并放行 mock 产生的未配置价格节点。
                         不免除任何付费保护：仍须 --budget-yuan 和服务端每日上限预检；开跑前还会检查后端
                         没有已启用的真实图片 / 视频 / 对话部署，有就拒绝。结果不能用于基准判定，会醒目警告

预算保护（真实跑）：
  1. 开跑前读后端的“每日云端花费上限”（DAILY_BUDGET_CNY，随画布 /runs 的 budget 字段返回）。上限为空（不限），
     或高于 --budget-yuan + 今日已花（含在途）时直接拒绝启动，并给出应设置的值：这是服务端硬上限，真正兜底。
  2. 每一步运行前先批量报价，要求 报价 × worker 最大尝试次数
     + 累计已花 <= --budget-yuan。拆分同样先估算（token 数 × 单价 × 3 倍余量，覆盖自动修复和重试）再调用。
  3. 报价里“未配置价格”的节点默认拒绝（--allow-unpriced 才放行）。
  4. 实际花费一律取自接口（画布 /runs 的 spent，即用量事件）；拆分未归入画布用量时，按估算的单次费用保守计入。
  5. 轮询异常 / 超时：先取消批次，再等批次内所有任务进入终态（上限 --settle-timeout），再读花费入账，之后才可能重试；
     在途任务超时仍未结算则停止整个基准，不重试。
  6. 预算 / 结算 / mock 触发的停止都会保留该用例的完整记录（画布 id、花费、耗时、步骤），状态记 budget_stopped /
     invalid_mock，按失败计入成功率分母，花费计入总花费；尚未开始的用例记 not_run，不进分母。

真实性校验：
  真实跑通常要开 ALLOW_MOCK_PROVIDERS=true（没有真实 TTS key，配音要回退 mock_tts），但这也会让图片 / 视频 / 拆分
  静默变成 mock。所以脚本逐一核对：拆分结果的 mocked 标记、每个图片 / 视频任务的 result.mocked 与 provider / model。
  任何一个是 mock 则该用例记 invalid_mock（失败）并停止基准。配音允许是 mock，报告会写明“配音为模拟（无真实 TTS
  key）”，该用例的配音花费记 0。

预估算法（真实跑之前用 --dry-quote）：
  总预估 = Σ 用例 (批量报价(关键帧 + 视频，含尚未生成的上游占位) + 批量报价(配音))，不含拆分的 LLM 费用（极小）。
  图片 = 单价 × 张数（角色设定图未上传时才生成，关键帧每镜头 1 张）；视频 = 模型路由按“时长 × 分辨率档位”的单价；
  配音 = 字符数 × 单价。渲染（本地 ffmpeg）不计费。预算上限建议取总预估 × 尝试次数余量。

汇总口径：
  成功 = 导出了成片且 qc 无阻止项（failed）。成功率分母 = 实际开始的用例（含失败、budget_stopped、invalid_mock）。
  qc 通过率分母 = 所有导出了文件的用例（含 qc failed）。达到退出标准需同时满足：实际运行用例 >= 10、成功率 >= 90%、
  所有导出文件无 qc failed、没有 mock 图片 / 视频 / 拆分、没有因预算 / 结算停止。不足 10 个用例报告写“规模不足以判定退出标准”。

退出码：0 达标（或 --dry-quote 完成）；1 未达标；2 参数 / 登录 / 预算上限校验错误；3 因预算、结算或 mock 停止。
"""

from __future__ import annotations

import argparse
import json
import math
import mimetypes
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_FILE = REPO_ROOT / "backend" / "app" / "eval_data" / "vistory_v1.json"

SUCCESS_RATE_TARGET = 0.9
MIN_CASES = 10
# 视频清晰度档 → 节点 tier（见模块说明）
TIER_BY_RESOLUTION = {"480p": "preview", "720p": "final", "1080p": "final"}
STEPS = ("split", "characters", "keyframes", "videos", "voices", "render")
TERMINAL_JOB = {"succeeded", "failed", "canceled", "dead_lettered"}
MOCK_PROVIDERS = {"mock", "mock_image", "mock_tts"}
# 拆分提示词（SPLIT_PROMPT）的大致字数，用于估算输入 token
SPLIT_PROMPT_CHARS = 450
SPLIT_RETRY_HEADROOM = 3.0

# 布局常量与前端 graph.ts 的 layoutSplit 一致
COLUMN = {"character": 0, "keyframe": 420, "video": 840, "audio": 1260}
ROW = 700
LINE_STEP = 230
NARRATOR = "旁白"

# dry-quote 模式下绝不允许发出的写请求（收费或会触发生成的接口）
PAID_PATTERNS = (
    r"/copilot/split$",
    r"/batch-runs(/|$)",
    r"/nodes/[^/]+/run$",
    r"/final/render$",
    r"/eval/runs$",
)


# ── 异常 ──────────────────────────────────────────────────────────────────


class StepFailed(Exception):
    """某一步失败（重试后仍失败）。reason 写进报告。"""

    def __init__(self, step: str, reason: str):
        super().__init__(f"{step}: {reason}")
        self.step = step
        self.reason = reason


class BudgetStop(Exception):
    """预算不足、在途任务无法结算等：整个基准停止。step 由抛出处或 run_case 补全。"""

    def __init__(self, reason: str, step: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.step = step


class MockDetected(Exception):
    """图片 / 视频 / 拆分的结果来自 mock：该用例无效，基准停止。"""

    def __init__(self, step: str, reason: str):
        super().__init__(reason)
        self.step = step
        self.reason = reason


class DryRunViolation(RuntimeError):
    """--dry-quote 下试图调用收费接口（代码缺陷，立即终止）。"""


class ApiError(Exception):
    def __init__(self, status: int, message: str, response: Optional[httpx.Response] = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.response = response


# ── 纯逻辑：预算 ──────────────────────────────────────────────────────────


@dataclass
class BudgetGuard:
    """总预算守卫。

    通过条件：累计已花 + 报价 × 倍数 <= 上限。倍数 = worker 最大尝试次数（任务失败重试不会再经过脚本），
    报价与落账口径是否一致由开跑前的价格体检（/api/eval/price-consistency）保证。limit 为 None 表示不限（仅 mock 用）。
    """

    limit: Optional[float]
    spent: float = 0.0
    allow_unpriced: bool = False
    attempts: int = 3
    unpriced_nodes: int = 0

    def remaining(self) -> Optional[float]:
        return None if self.limit is None else max(0.0, self.limit - self.spent)

    def multiplier(self, kind: str) -> float:
        return float(self.attempts)

    def check(
        self,
        estimate: float,
        *,
        kind: str = "video",
        unpriced: int = 0,
        label: str = "",
        multiplier: Optional[float] = None,
    ) -> None:
        """通过则返回 None；价格未知（默认）或预算不够抛 BudgetStop。"""
        if unpriced and not self.allow_unpriced:
            raise BudgetStop(
                f"{label} 有 {unpriced} 个收费节点未配置价格（需 --allow-unpriced 才放行）", label
            )
        factor = self.multiplier(kind) if multiplier is None else multiplier
        worst = estimate * factor
        if self.limit is not None and self.spent + worst > self.limit + 1e-9:
            raise BudgetStop(
                f"{label} 报价 ¥{estimate:.2f} × 最大 {factor:g} 倍（尝试次数）= ¥{worst:.2f}，"
                f"累计已花 ¥{self.spent:.2f}，超出总预算 ¥{self.limit:.2f}",
                label,
            )
        self.unpriced_nodes += unpriced

    def record(self, actual: float) -> None:
        self.spent += max(0.0, actual)


def quote_unpriced(quote: dict[str, Any]) -> int:
    """批量报价里“照常运行但价格未知”的节点数（estimated_cost 为 None 且没有阻止项）。"""
    return sum(
        1
        for item in quote.get("nodes", [])
        if item.get("status") == "pending"
        and item.get("estimated_cost") is None
        and not item.get("blockers")
        and not item.get("waits_upstream")
    )


def estimate_split_cost(idea: str, shots: int, input_price: float, output_price: float) -> float:
    """一次拆分请求的保守费用（元）。

    输入按“1 字 1 token”，输出按每镜头约 260 token；后端格式错误时会自动修复重试一次（带上上一轮输出再请求），
    所以输入和输出都乘 2。单价是元 / 百万 token。守卫再按 SPLIT_RETRY_HEADROOM 倍预留。
    """
    tokens_in = SPLIT_PROMPT_CHARS + len(idea)
    tokens_out = 300 + 260 * max(1, shots)
    return (tokens_in * input_price * 2 + tokens_out * output_price * 2) / 1_000_000


def check_daily_cap(budget: dict[str, Any], limit: float) -> Optional[str]:
    """服务端每日云端花费上限校验：合格返回 None，否则返回应展示给操作者的拒绝原因。

    上限就是真正的兜底：上限必须不超过 --budget-yuan + 今日已花（含在途）。上限更小只会让服务端更早拒绝，允许。
    """
    cap = budget.get("cap")
    used = float(budget.get("spent") or 0) + float(budget.get("in_flight") or 0)
    target = limit + used
    if cap is None:
        return (
            f"后端没有设每日云端花费上限（DAILY_BUDGET_CNY 为空或 0 表示不限），脚本拒绝启动。"
            f"请在后端环境变量里设 DAILY_BUDGET_CNY={target:.2f}（= --budget-yuan {limit:g} + 今日已花 ¥{used:.2f}）后重启"
        )
    if float(cap) > target + 0.01:
        return (
            f"后端每日云端花费上限 ¥{float(cap):.2f} 高于 --budget-yuan {limit:g} + 今日已花 ¥{used:.2f}，"
            f"脚本拒绝启动。请把后端 DAILY_BUDGET_CNY 设为 {target:.2f} 后重启"
        )
    return None


# ── 纯逻辑：基准集 → 想法文本 / 画布图 ────────────────────────────────────


def select_stories(stories: list[dict[str, Any]], selector: str, min_shots: int = 5) -> list[dict[str, Any]]:
    """--stories：纯数字取前 N 个镜头数 >= min_shots 的故事；否则按逗号分隔的 key 取。"""
    selector = str(selector).strip()
    if selector.isdigit():
        eligible = [story for story in stories if len(story.get("shots") or []) >= min_shots]
        return eligible[: int(selector)]
    by_key = {story["key"]: story for story in stories}
    keys = [key.strip() for key in selector.split(",") if key.strip()]
    missing = [key for key in keys if key not in by_key]
    if missing:
        raise ValueError(f"基准集里没有这些故事：{', '.join(missing)}")
    return [by_key[key] for key in keys]


def story_characters(story: dict[str, Any], max_shots: int, limit: int = 4) -> list[dict[str, Any]]:
    """截断后的镜头里出镜的角色（按首次出场顺序），最多 limit 个。"""
    by_key = {item["key"]: item for item in story.get("characters") or []}
    seen: list[str] = []
    for shot in (story.get("shots") or [])[:max_shots]:
        for key in shot.get("characters") or []:
            if key in by_key and key not in seen:
                seen.append(key)
    return [by_key[key] for key in seen[:limit]]


def build_idea(story: dict[str, Any], max_shots: int) -> str:
    """Copilot 拆分用的想法文本：角色设定 + 逐镜头的结构化描述（模型可改写，mock 直接采用）。"""
    characters = story_characters(story, max_shots)
    names = {item["key"]: item["name"] for item in story.get("characters") or []}
    lines = [f"故事：{story.get('name', '')}"]
    lines += [f"角色：{item['name']}：{item['prompt']}" for item in characters]
    for index, shot in enumerate((story.get("shots") or [])[:max_shots], 1):
        present = "、".join(names[key] for key in shot.get("characters") or [] if key in names)
        lines.append(
            f"镜头{index}：{shot.get('plot', '')}｜画面：{shot.get('description', '')}"
            f"｜环境：{shot.get('setting', '')}｜镜头：{shot.get('camera', '')}｜出镜：{present}"
        )
    return "\n".join(lines)


def local_split(story: dict[str, Any], max_shots: int) -> dict[str, Any]:
    """不调用任何模型的“假拆分”（--dry-quote 用）：直接用基准集的镜头情节 / 画面描述拼出 SplitResult 形状。"""
    names = {item["key"]: item["name"] for item in story.get("characters") or []}
    shots = []
    for index, shot in enumerate((story.get("shots") or [])[:max_shots], 1):
        parts = [shot.get("description"), shot.get("setting"), shot.get("camera")]
        plot = str(shot.get("plot") or "").strip()
        shots.append(
            {
                "title": f"镜头{index}",
                "prompt": "，".join(str(p) for p in parts if p)[:780] or plot or f"镜头{index}",
                "characters": [names[k] for k in shot.get("characters") or [] if k in names][:3],
                "duration_s": 4,
                "lines": [{"speaker": NARRATOR, "text": plot[:60]}] if plot else [],
            }
        )
    return {
        "characters": [
            {"name": item["name"], "description": item["prompt"]}
            for item in story_characters(story, max_shots)
        ],
        "shots": shots,
        "mocked": False,
    }


def match_sheet(name: str, story: dict[str, Any]) -> Optional[dict[str, Any]]:
    """拆分出的角色名 → 基准集里带设定图的角色：名字相同，其次英文名 / 互相包含。"""
    candidates = [item for item in story.get("characters") or [] if item.get("reference_image")]
    lowered = name.strip().lower()
    for item in candidates:
        if item["name"] == name.strip():
            return item
    for item in candidates:
        if item.get("name_en", "").lower() == lowered:
            return item
    for item in candidates:
        if item["name"] in name or name in item["name"]:
            return item
    return None


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def edge_id(source: str, target: str, handle: str) -> str:
    return f"e-{source}-{target}-{handle}"


def layout_graph(
    split: dict[str, Any], *, video_tier: str, image_model: str = "", video_model: str = ""
) -> tuple[dict[str, Any], dict[str, str], list[dict[str, Any]]]:
    """把拆分结果摆成画布（与前端 layoutSplit 同构）：角色图 → 关键帧 → 视频 ← 台词配音。

    返回 (graph, {角色名: 角色节点 id}, 每个镜头的节点 [{keyframe, video, audios}])。数量 count 都取 1，控制花费。
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    character_ids: dict[str, str] = {}
    shot_nodes: list[dict[str, Any]] = []
    for index, character in enumerate(split.get("characters") or []):
        node_id = new_id("image")
        character_ids[character["name"]] = node_id
        nodes.append(
            {
                "id": node_id,
                "type": "image",
                "position": {"x": COLUMN["character"], "y": index * ROW},
                "data": {
                    "label": f"角色 · {character['name']}",
                    "prompt": f"{character['name']}，{character.get('description', '')}，角色定妆照，正面半身，纯色背景",
                    "model": image_model,
                    "count": 1,
                    "role": "character",
                    "character": character["name"],
                },
            }
        )
    for index, shot in enumerate(split.get("shots") or []):
        y = index * ROW
        keyframe, video = new_id("image"), new_id("video")
        audios: list[str] = []
        title = shot.get("title") or f"镜头 {index + 1}"
        nodes.append(
            {
                "id": keyframe,
                "type": "image",
                "position": {"x": COLUMN["keyframe"], "y": y},
                "data": {
                    "label": f"{index + 1} · {title} 关键帧",
                    "prompt": shot["prompt"],
                    "model": image_model,
                    "count": 1,
                },
            }
        )
        nodes.append(
            {
                "id": video,
                "type": "video",
                "position": {"x": COLUMN["video"], "y": y},
                "data": {
                    "label": f"{index + 1} · {title}",
                    "prompt": shot["prompt"],
                    "model": video_model,
                    "duration": shot.get("duration_s") or 5,
                    "tier": video_tier,
                    "order": index,
                },
            }
        )
        for name in (shot.get("characters") or [])[:4]:
            source = character_ids.get(name)
            if source:
                edges.append(
                    {
                        "id": edge_id(source, keyframe, "reference"),
                        "source": source,
                        "target": keyframe,
                        "sourceHandle": "out",
                        "targetHandle": "reference",
                    }
                )
        edges.append(
            {
                "id": edge_id(keyframe, video, "first_frame"),
                "source": keyframe,
                "target": video,
                "sourceHandle": "out",
                "targetHandle": "first_frame",
            }
        )
        for order, line in enumerate(shot.get("lines") or []):
            text = str(line.get("text") or "").strip()
            if not text:
                continue
            speaker = str(line.get("speaker") or "").strip() or NARRATOR
            audio = new_id("audio")
            audios.append(audio)
            nodes.append(
                {
                    "id": audio,
                    "type": "audio",
                    "position": {"x": COLUMN["audio"], "y": y + order * LINE_STEP},
                    "data": {
                        "label": f"台词 · {speaker}",
                        "speaker": speaker,
                        "text": text,
                        "voice": "",
                        "order": order,
                    },
                }
            )
            edges.append(
                {
                    "id": edge_id(audio, video, "voice"),
                    "source": audio,
                    "target": video,
                    "sourceHandle": "out",
                    "targetHandle": "voice",
                }
            )
        shot_nodes.append({"keyframe": keyframe, "video": video, "audios": audios})
    graph = {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 0.5}}
    return graph, character_ids, shot_nodes


# ── 纯逻辑：任务时间 / 真实性 ─────────────────────────────────────────────


def _parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def job_seconds(job: dict[str, Any]) -> Optional[float]:
    """任务真实耗时（秒）= finished_at - started_at。

    图片 / 配音 worker 不写 started_at，这时退而用 created_at（含排队时间，仍是实测值）；
    起点或终点都拿不到就返回 None，不用别的数凑。
    """
    started = _parse_time(job.get("started_at")) or _parse_time(job.get("created_at"))
    finished = _parse_time(job.get("finished_at"))
    if started is None or finished is None or finished < started:
        return None
    return (finished - started).total_seconds()


def job_is_mock(job: dict[str, Any]) -> bool:
    """任务结果是否来自 mock：result.mocked、provider / model 名、锁定的部署名任一指向 mock。"""
    result = job.get("result") or {}
    params = job.get("params") or {}
    snapshot = params.get("routing_snapshot") or {}
    names = [
        str(job.get("provider") or ""),
        str(job.get("model") or ""),
        str(snapshot.get("provider") or ""),
        str(params.get("deployment_name") or snapshot.get("deployment_name") or ""),
    ]
    return (
        result.get("mocked") is True
        or any(name in MOCK_PROVIDERS for name in names)
        or any(name.startswith(("mock", "legacy-mock")) for name in names if name)
    )


def shot_latencies_ms(shot_nodes: list[dict[str, Any]], seconds: dict[str, float]) -> list[Optional[float]]:
    """每个镜头的真实耗时（毫秒）= 关键帧 + 视频 + 其配音任务耗时之和；关键帧 / 视频任一缺时间则为 None。"""
    out: list[Optional[float]] = []
    for shot in shot_nodes:
        base = [seconds.get(shot["keyframe"]), seconds.get(shot["video"])]
        if any(item is None for item in base):
            out.append(None)
            continue
        voices = [seconds[a] for a in shot["audios"] if a in seconds]
        out.append((sum(base) + sum(voices)) * 1000)
    return out


# ── 纯逻辑：统计与报告 ────────────────────────────────────────────────────


def percentile(values: list[float], percent: float) -> Optional[float]:
    """线性插值分位数（与 eval_service.percentile 同口径）；空列表返回 None。"""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _stat(values: list[float]) -> dict[str, Optional[float]]:
    return {"avg": _mean(values), "p50": percentile(values, 0.5), "p95": percentile(values, 0.95)}


def summarize(cases: list[dict[str, Any]], *, mock_media: bool = False) -> dict[str, Any]:
    """汇总：成功率、qc 通过率、每集平均成本 / 耗时与 p50 / p95、是否达标。

    - 成功率分母 = 实际开始的用例（status != not_run），含失败、budget_stopped、invalid_mock（都按失败）。
    - qc 分母 = 所有导出了成片文件的用例（有 final_path），含 qc failed 的。
    - 成本、耗时的平均 / 分位数只统计成功导出的用例；total_all_cases 含全部已开始用例的花费。
    - 达标：实际运行 >= 10 个、没有 not_run、成功率 >= 90%、导出文件无 qc failed、没有 mock 媒体、没有被停止的用例。
    """
    ran = [case for case in cases if case.get("status") != "not_run"]
    done = [case for case in ran if case.get("ok")]
    exported = [case for case in ran if case.get("final_path")]
    qc_statuses = [(case.get("qc") or {}).get("status") for case in exported]
    qc_passed = sum(1 for status in qc_statuses if status == "passed")
    qc_warning = sum(1 for status in qc_statuses if status == "warning")
    qc_failed = sum(1 for status in qc_statuses if status not in ("passed", "warning"))
    durations = [float(case["duration_s"]) for case in done if case.get("duration_s") is not None]
    costs = [float(case["cost_yuan"]) for case in done if case.get("cost_yuan") is not None]
    step_times: dict[str, list[float]] = {step: [] for step in STEPS}
    for case in done:
        for step, seconds in (case.get("step_seconds") or {}).items():
            if step in step_times:
                step_times[step].append(float(seconds))
    failed_steps: dict[str, int] = {}
    for case in ran:
        if not case.get("ok"):
            step = case.get("failed_step") or "unknown"
            failed_steps[step] = failed_steps.get(step, 0) + 1
    statuses: dict[str, int] = {}
    for case in cases:
        key = case.get("status") or ("ok" if case.get("ok") else "failed")
        statuses[key] = statuses.get(key, 0) + 1
    success_rate = len(done) / len(ran) if ran else 0.0
    stopped = any(case.get("status") in ("budget_stopped", "invalid_mock") for case in ran)
    sufficient = len(ran) >= MIN_CASES
    mock_used = mock_media or any(case.get("mock_media") for case in ran)
    meets = (
        sufficient
        and len(ran) == len(cases)
        and success_rate >= SUCCESS_RATE_TARGET
        and qc_failed == 0
        and not stopped
        and not mock_used
    )
    return {
        "total": len(cases),
        "ran": len(ran),
        "not_run": len(cases) - len(ran),
        "succeeded": len(done),
        "failed": len(ran) - len(done),
        "statuses": statuses,
        "success_rate": success_rate,
        "exported": len(exported),
        "qc_passed": qc_passed,
        "qc_warning": qc_warning,
        "qc_failed": qc_failed,
        "qc_pass_rate": qc_passed / len(exported) if exported else 0.0,
        "qc_no_blocking_rate": (qc_passed + qc_warning) / len(exported) if exported else 0.0,
        "cost_yuan": {**_stat(costs), "total_all_cases": sum(float(c.get("cost_yuan") or 0) for c in ran)},
        "duration_s": _stat(durations),
        "step_seconds_avg": {step: _mean(values) for step, values in step_times.items() if values},
        "failed_steps": failed_steps,
        "sufficient_scale": sufficient,
        "mock_media": mock_used,
        "tts_mocked_cases": sum(1 for case in ran if case.get("tts_mocked")),
        "meets_exit_criteria": meets,
    }


def verdict_text(summary: dict[str, Any]) -> str:
    if summary["mock_media"]:
        return "不适用：本次含 mock 图片 / 视频 / 拆分，结果不能用于基准判定"
    if not summary["sufficient_scale"]:
        return f"规模不足以判定退出标准（实际运行 {summary['ran']} 个用例，至少需要 {MIN_CASES} 个）"
    return "是" if summary["meets_exit_criteria"] else "否"


def _fmt(value: Optional[float], digits: int = 2, unit: str = "") -> str:
    return "-" if value is None else f"{value:.{digits}f}{unit}"


STATUS_LABEL = {
    "ok": "成功",
    "failed": "失败",
    "not_run": "未运行",
    "budget_stopped": "预算 / 结算停止（按失败计）",
    "invalid_mock": "无效：结果来自 mock（按失败计）",
}


def render_markdown(report: dict[str, Any]) -> str:
    """report.json 的人读版本。"""
    summary = report["summary"]
    budget = report["budget"]
    lines = [f"# 画布端到端基准报告 · {report['started_at']}", ""]
    if summary["mock_media"]:
        lines += [
            "> **警告：本次允许图片 / 视频 / 拆分使用 mock（--allow-mock-media），结果不能用于基准判定。**",
            "",
        ]
    lines += [
        f"- 后端：{report['base_url']}　清晰度档：{report['args']['resolution']}（tier={report['video_tier']}）",
        f"- 视频模型：{report['args'].get('video_model') or '默认路由'}　图片模型：{report['args'].get('image_model') or '默认路由'}",
        f"- 每例镜头数上限：{report['args']['max_shots']}　预算上限：{'不限' if budget['limit'] is None else '¥%.2f' % budget['limit']}"
        f"　累计实际花费：¥{budget['spent']:.4f}",
    ]
    if report.get("dry_quote"):
        lines += [
            "- 模式：只报价（--dry-quote），未调用拆分 / 批量运行等任何收费接口，镜头由基准集文本本地拼出"
        ]
    if budget.get("stopped_reason"):
        lines += [f"- **基准被停止**：{budget['stopped_reason']}"]
    if budget.get("unpriced_nodes"):
        lines += [f"- 注意：{budget['unpriced_nodes']} 个节点未配置价格（--allow-unpriced），未计入花费"]
    split_total = sum(float(case.get("split_cost_estimated") or 0) for case in report["cases"])
    if split_total:
        lines += [
            f"- 拆分（LLM）费用：估算 ¥{split_total:.4f}（LLM 免费额度内可能为 0），已按估算计入累计花费"
        ]
    if summary["tts_mocked_cases"]:
        lines += [
            f"- 配音为模拟（无真实 TTS key）：{summary['tts_mocked_cases']} 个用例，这些用例的配音花费记 0"
        ]
    lines += ["", "## 汇总", ""]
    if report.get("dry_quote"):
        quote = report["dry_quote"]
        lines += [
            f"- 总预估：¥{quote['estimated_total']:.2f}（关键帧 + 视频 ¥{quote['media_total']:.2f}，配音 ¥{quote['voice_total']:.2f}）",
            f"- 用例数 {summary['total']}，其中未配置价格的节点 {quote['unpriced_nodes']} 个",
        ]
    else:
        cost, duration = summary["cost_yuan"], summary["duration_s"]
        lines += [
            f"- 成功率：{summary['succeeded']}/{summary['ran']} = {summary['success_rate']:.0%}"
            f"（目标 >= {SUCCESS_RATE_TARGET:.0%}；分母含失败、预算 / 结算停止、mock 无效的用例）　未开始：{summary['not_run']}",
            f"- 出片检查（分母 = 导出了文件的 {summary['exported']} 个用例，含 qc 失败的）：完全通过 {summary['qc_passed']}，"
            f"警告 {summary['qc_warning']}，失败 {summary['qc_failed']}",
            f"- 每集成本（元）：平均 {_fmt(cost['avg'], 4)}　p50 {_fmt(cost['p50'], 4)}　p95 {_fmt(cost['p95'], 4)}"
            f"　全部已开始用例合计 {_fmt(cost['total_all_cases'], 4)}",
            f"- 每集耗时（秒）：平均 {_fmt(duration['avg'], 1)}　p50 {_fmt(duration['p50'], 1)}　p95 {_fmt(duration['p95'], 1)}",
            f"- 各步平均耗时（秒）：{'　'.join(f'{k} {v:.1f}' for k, v in summary['step_seconds_avg'].items()) or '-'}",
            f"- 失败步骤分布：{summary['failed_steps'] or '无'}",
            f"- 是否达到阶段 1 退出标准：**{verdict_text(summary)}**",
        ]
    lines += [
        "",
        "## 用例",
        "",
        "| 故事 | 结果 | 失败步骤 | 镜头 | 耗时(s) | 花费(元) | qc | 成片 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for case in report["cases"]:
        qc = (case.get("qc") or {}).get("status") or "-"
        status = case.get("status") or ("ok" if case.get("ok") else "failed")
        lines.append(
            f"| {case['story_key']} {case.get('name', '')} | {STATUS_LABEL.get(status, status)} | {case.get('failed_step') or '-'} "
            f"| {case.get('shots', '-')} | {_fmt(case.get('duration_s'), 1)} | {_fmt(case.get('cost_yuan'), 4)} "
            f"| {qc} | {case.get('final_path') or '-'} |"
        )
    problems = [case for case in report["cases"] if case.get("error") or (case.get("qc") or {}).get("issues")]
    if problems:
        lines += ["", "## 失败原因与 qc 问题", ""]
        for case in problems:
            lines.append(f"### {case['story_key']} {case.get('name', '')}")
            if case.get("error"):
                label = STATUS_LABEL.get(case.get("status") or "failed", "失败")
                lines.append(f"- {label}：{case['error']}（重试 {case.get('retries', 0)} 次）")
            for issue in (case.get("qc") or {}).get("issues") or []:
                lines.append(
                    f"- qc [{issue.get('severity') or issue.get('level') or ''}] {issue.get('code', '')}: {issue.get('message', '')}"
                )
            lines.append("")
    for item in report.get("eval_runs") or []:
        lines.append(f"- EvalRun {item.get('story_key')}：{item.get('id') or item.get('error')}")
    return "\n".join(lines).rstrip() + "\n"


def build_eval_samples(case: dict[str, Any]) -> list[dict[str, Any]]:
    """一个用例 → /api/eval/runs 的 samples（每个镜头一条）。

    只填能从运行结果直接得到的字段：success、latency_ms（该镜头自己的任务真实耗时，缺则不填）、cost_cny
    （用例总花费按镜头数平均，接口拿不到分镜头花费）、failure_reasons；qc 通过映射为 technical_quality，
    音画时长检查映射为 av_sync，其余质量维度不编造。
    """
    shots = max(1, int(case.get("shots") or 1))
    ok = bool(case.get("ok"))
    qc = case.get("qc") or {}
    checks = qc.get("checks") or {}
    latencies = case.get("shot_latency_ms") or []
    samples = []
    for index in range(shots):
        sample: dict[str, Any] = {
            "success": ok,
            "cost_cny": (case["cost_yuan"] / shots) if case.get("cost_yuan") is not None else None,
            "failure_reasons": []
            if ok
            else [f"{case.get('failed_step') or 'unknown'}: {case.get('error') or ''}"[:200]],
        }
        latency = latencies[index] if index < len(latencies) else None
        if latency is not None:
            sample["latency_ms"] = latency
        if qc.get("status") in ("passed", "warning", "failed"):
            sample["technical_quality"] = {"passed": 1.0, "warning": 0.7, "failed": 0.0}[qc["status"]]
        av = (checks.get("av_duration_diff_ms") or {}).get("status")
        if av in ("passed", "failed"):
            sample["av_sync"] = 1.0 if av == "passed" else 0.0
        samples.append(sample)
    return samples


def error_message(response: httpx.Response) -> str:
    """接口错误响应 → 一句话原因。"""
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code} {response.text[:200]}"
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:300]
        if body.get("detail"):
            return str(body["detail"])[:300]
    return f"HTTP {response.status_code}"


# ── HTTP 客户端 ───────────────────────────────────────────────────────────


class Api:
    """薄封装：登录一次，GET 对网络抖动 / 5xx 重试，其余请求不自动重试（靠 client_request_id 幂等）。

    forbid_paid=True（--dry-quote）时，任何命中 PAID_PATTERNS 的写请求在发出前就抛 DryRunViolation。
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout: float = 60,
        *,
        forbid_paid: bool = False,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.forbid_paid = forbid_paid
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport)
        response = self.client.post("/api/auth/login", json={"username": username, "password": password})
        if response.status_code != 200:
            raise PermissionError(f"登录失败：{error_message(response)}")
        self.client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if self.forbid_paid and method != "GET" and any(re.search(p, path) for p in PAID_PATTERNS):
            raise DryRunViolation(f"--dry-quote 下禁止调用 {method} {path}")
        attempts = 3 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                response = self.client.request(method, path, **kwargs)
            except httpx.TransportError:
                if attempt == attempts - 1:
                    raise
            else:
                if response.status_code < 500 or attempt == attempts - 1:
                    return response
            time.sleep(1 + attempt)
        raise RuntimeError("unreachable")

    def json(self, method: str, path: str, ok: tuple[int, ...] = (200, 201, 202), **kwargs: Any) -> Any:
        response = self.request(method, path, **kwargs)
        if response.status_code not in ok:
            raise ApiError(response.status_code, error_message(response), response)
        return response.json() if response.content else None


# ── 单个用例 ──────────────────────────────────────────────────────────────


@dataclass
class Settings:
    max_shots: int
    video_tier: str
    image_model: str
    video_model: str
    step_timeout: float
    render_timeout: float
    settle_timeout: float
    poll_interval: float
    sheets_dirs: list[Path]
    run_dir: Path
    dry_quote: bool = False
    allow_mock_media: bool = False
    llm_input_price: float = 4.0
    llm_output_price: float = 16.0
    max_split_calls: int = 1000


@dataclass
class CaseCtx:
    """一个用例的运行上下文（不进报告）：节点类型、镜头节点、任务耗时、已入账花费。"""

    canvas_id: str = ""
    node_kinds: dict[str, str] = field(default_factory=dict)
    shot_nodes: list[dict[str, Any]] = field(default_factory=list)
    job_seconds: dict[str, float] = field(default_factory=dict)
    accounted: float = 0.0  # 已计入守卫的画布花费
    ignored: float = 0.0  # 不计入的花费（模拟配音）
    split_extra: float = 0.0  # 拆分估算里画布用量没覆盖的部分
    tts_mocked: bool = False
    mock_media: bool = False


def find_sheet(relative: str, dirs: list[Path]) -> Optional[Path]:
    for base in dirs:
        candidate = base / relative
        if candidate.is_file():
            return candidate
    return None


def default_sheet_dirs() -> list[Path]:
    """设定图目录候选：本仓库 backend/storage，以及（worktree 时）同级主仓库的 backend/storage。"""
    dirs = [REPO_ROOT / "backend" / "storage"]
    for parent in REPO_ROOT.parents[:3]:
        for name in ("fishstudio", "FishStudio"):
            candidate = parent / name / "backend" / "storage"
            if candidate.is_dir() and candidate not in dirs:
                dirs.append(candidate)
    return dirs


class Runner:
    def __init__(self, api: Api, settings: Settings, guard: BudgetGuard, log: Callable[[str], None] = print):
        self.api = api
        self.cfg = settings
        self.guard = guard
        self.log = log
        self.stop_reason = ""
        self.split_calls = 0

    # ---- 画布读写 ----
    def canvas(self, canvas_id: str) -> dict[str, Any]:
        return self.api.json("GET", f"/api/node-canvases/{canvas_id}")

    def spent(self, canvas_id: str) -> float:
        """画布累计实际花费（接口给的，不自己估）。"""
        runs = self.api.json("GET", f"/api/node-canvases/{canvas_id}/runs")
        return float(runs.get("spent") or 0)

    def sync_spend(self, ctx: CaseCtx, *, ignore: bool = False) -> float:
        """读画布累计花费，把新增部分计入守卫（ignore=True 的部分只记 ignored，不计入）。返回新增量。"""
        now = self.spent(ctx.canvas_id)
        delta = max(0.0, now - ctx.accounted)
        ctx.accounted = max(ctx.accounted, now)
        if ignore:
            ctx.ignored += delta
        else:
            self.guard.record(delta)
        return delta

    def case_cost(self, ctx: CaseCtx) -> float:
        return max(0.0, ctx.accounted - ctx.ignored) + ctx.split_extra

    # ---- 在途任务结算（异常 / 超时路径）----
    def job(self, job_id: str) -> dict[str, Any]:
        return self.api.json("GET", f"/api/jobs/{job_id}")

    def job_terminal(self, job_id: str) -> bool:
        job = self.job(job_id)
        return job["status"] in TERMINAL_JOB or bool(job.get("dead_lettered_at"))

    def settle(self, ctx: CaseCtx, step: str) -> None:
        """取消这张画布上所有运行中的批次，等其全部任务进入终态；超时抛 BudgetStop（调用方不得再重试）。"""
        canvas_id = ctx.canvas_id
        deadline = time.monotonic() + self.cfg.settle_timeout
        cancelled: set[str] = set()
        try:
            while True:
                pending = False
                for batch in self.api.json("GET", f"/api/node-canvases/{canvas_id}/batch-runs"):
                    if batch["status"] == "running" and batch["id"] not in cancelled:
                        cancelled.add(batch["id"])
                        try:
                            batch = self.api.json(
                                "POST", f"/api/node-canvases/{canvas_id}/batch-runs/{batch['id']}/cancel"
                            )
                        except ApiError:
                            pass
                    if batch["status"] == "running":
                        pending = True
                    for state in (batch.get("nodes") or {}).values():
                        job_id = state.get("job_id")
                        if job_id and not self.job_terminal(job_id):
                            pending = True
                if not pending:
                    return
                if time.monotonic() >= deadline:
                    break
                time.sleep(self.cfg.poll_interval)
        except (ApiError, httpx.HTTPError) as error:
            raise BudgetStop(
                f"取消后无法确认在途任务状态（{str(error)[:100]}），停止基准，请人工核对花费", step
            ) from error
        raise BudgetStop(
            f"取消批次后 {self.cfg.settle_timeout:.0f} 秒内仍有在途任务未结算，停止基准（不重试），请人工核对花费",
            step,
        )

    # ---- 任务核对：真实性 + 耗时 ----
    def inspect_jobs(self, ctx: CaseCtx, detail: dict[str, Any]) -> dict[str, Any]:
        """逐个核对批次里的任务：图片 / 视频是否 mock、配音是否 mock、worker 最大尝试次数，并记录真实耗时。"""
        found: dict[str, Any] = {"mock_media": [], "tts_mocked": False, "attempts": 0}
        for node_id, state in (detail.get("nodes") or {}).items():
            job_id = state.get("job_id")
            if not job_id:
                continue
            job = self.job(job_id)
            kind = ctx.node_kinds.get(node_id, "")
            found["attempts"] = max(found["attempts"], int(job.get("max_attempts") or 0))
            if job_is_mock(job):
                if kind == "audio":
                    found["tts_mocked"] = True
                else:
                    found["mock_media"].append(kind or job.get("operation", "?"))
            seconds = job_seconds(job)
            if seconds is not None and state.get("status") == "succeeded":
                ctx.job_seconds[node_id] = seconds
        return found

    def flag_mock(self, ctx: CaseCtx, step: str, kinds: list[str]) -> None:
        """图片 / 视频来自 mock：默认判该用例 invalid_mock（抛 MockDetected）；--allow-mock-media 只做标记。"""
        if not self.cfg.allow_mock_media:
            kinds_text = "、".join(sorted(set(kinds)))
            raise MockDetected(step, f"{step} 的结果来自 mock（{kinds_text}），不是真实厂商")
        ctx.mock_media = True

    def verify_media(self, ctx: CaseCtx, step: str) -> None:
        """核对画布上所有图片 / 视频节点的全部任务（含已被取消批次的、以及被 skip_succeeded 复用的旧产物）的来源。

        批量运行的节点状态为 skipped 时 job_id 为空，inspect_jobs 看不到它们；这里改从 /runs 取每个节点的全部任务，
        逐个查 provider / mocked。上传的设定图没有任务，不在此列。配音节点只记录是否模拟。
        """
        runs = self.api.json("GET", f"/api/node-canvases/{ctx.canvas_id}/runs")
        mocked: list[str] = []
        for node_id, kind in ctx.node_kinds.items():
            for item in ((runs.get("nodes") or {}).get(node_id) or {}).get("jobs") or []:
                if not job_is_mock(self.job(item["id"])):
                    continue
                if kind == "audio":
                    ctx.tts_mocked = True
                else:
                    mocked.append(kind)
        if mocked:
            self.flag_mock(ctx, step, mocked)

    # ---- 批量步骤 ----
    def batch_step(self, ctx: CaseCtx, step: str, kinds: list[str], price_kind: str) -> dict[str, Any]:
        """报价 → 预算判断 → 运行 → 轮询 → 核对；失败重试一次（异常路径先取消并结算再重试）。"""
        canvas_id = ctx.canvas_id
        started = time.monotonic()
        last_error = ""
        for attempt in (1, 2):
            canvas = self.canvas(canvas_id)
            body: dict[str, Any] = {"scope": "all", "skip_succeeded": True, "kinds": kinds}
            try:
                quote = self.api.json("POST", f"/api/node-canvases/{canvas_id}/batch-quote", json=body)
            except ApiError as error:
                if "没有可运行" in error.message:
                    return {"seconds": 0.0, "attempts": attempt}
                raise StepFailed(step, f"报价失败：{error.message}") from error
            if quote.get("blocking_count"):
                blocked = next((n for n in quote["unavailable_nodes"] if n.get("blocks_batch")), {})
                raise StepFailed(
                    step, f"节点无法运行：{blocked.get('label', '')} {blocked.get('reason', '')}"[:300]
                )
            self.guard.check(
                float(quote.get("estimated_total") or 0),
                kind=price_kind,
                unpriced=quote_unpriced(quote),
                label=step,
            )
            try:
                batch = self.api.json(
                    "POST",
                    f"/api/node-canvases/{canvas_id}/batch-runs",
                    json={
                        **body,
                        "client_request_id": uuid.uuid4().hex,
                        "quoted_revision": canvas["revision"],
                    },
                )
                detail = self.wait_batch(canvas_id, batch["id"])
            except (ApiError, TimeoutError, httpx.HTTPError) as error:
                last_error = str(error)[:300]
                self.log(f"    {step} 第 {attempt} 次出错：{last_error}")
                # 先取消并等在途任务终态，再读花费入账；结算不了就停止整个基准，不重试
                try:
                    self.settle(ctx, step)
                finally:
                    self.sync_spend(ctx)
                # 旧批次的任务（已结算）和之后会被复用的产物都要核对：重试时它们会被 skip_succeeded 跳过
                self.verify_media(ctx, step)
                continue
            # 批次已结束（成功 / 失败 / 被取消）：先核对任务，再入账
            found = self.inspect_jobs(ctx, detail)
            mocked_tts = found["tts_mocked"] and step == "voices"
            self.sync_spend(ctx, ignore=mocked_tts)
            if mocked_tts:
                ctx.tts_mocked = True
            if found["attempts"] > self.guard.attempts:
                raise BudgetStop(
                    f"worker 对任务最多尝试 {found['attempts']} 次，大于假设的 {self.guard.attempts}，"
                    "请用 --worker-max-attempts 调整后重跑",
                    step,
                )
            if found["mock_media"] and step != "voices":
                self.flag_mock(ctx, step, found["mock_media"])
            if detail["status"] == "succeeded":
                return {"seconds": time.monotonic() - started, "attempts": attempt}
            last_error = batch_failure_reason(detail)
            self.log(f"    {step} 第 {attempt} 次未成功：{last_error}")
        raise StepFailed(step, last_error or "未知错误")

    def wait_batch(self, canvas_id: str, batch_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.cfg.step_timeout
        while time.monotonic() < deadline:
            detail = self.api.json("GET", f"/api/node-canvases/{canvas_id}/batch-runs/{batch_id}")
            if detail["status"] != "running":
                return detail
            time.sleep(self.cfg.poll_interval)
        raise TimeoutError(f"批量运行超过 {self.cfg.step_timeout:.0f} 秒未完成")

    # ---- 成片 ----
    def render_step(self, canvas_id: str, case_dir: Path) -> dict[str, Any]:
        started = time.monotonic()
        last_error = ""
        for attempt in (1, 2):
            try:
                revision = self.canvas(canvas_id)["revision"]
                ensured = self.api.json(
                    "POST",
                    f"/api/node-canvases/{canvas_id}/final/ensure-node",
                    json={"base_revision": revision},
                )
                revision = ensured["revision"]
                job = self.api.json(
                    "POST",
                    f"/api/node-canvases/{canvas_id}/final/render",
                    json={"base_revision": revision, "client_request_id": uuid.uuid4().hex},
                )
                entry = self.wait_render(canvas_id, job["job_id"])
            except ApiError as error:
                problems = ""
                if error.response is not None:
                    try:
                        problems = "；".join(
                            p.get("message", "") for p in error.response.json().get("problems", [])
                        )
                    except ValueError:
                        pass
                last_error = (problems or error.message)[:300]
            except (TimeoutError, httpx.HTTPError) as error:
                last_error = str(error)[:300]
            else:
                if entry["status"] == "succeeded":
                    return {
                        "seconds": time.monotonic() - started,
                        "attempts": attempt,
                        "qc": entry.get("qc"),
                        "final_path": self.download(entry, case_dir),
                    }
                last_error = entry.get("error") or f"渲染状态 {entry['status']}"
            self.log(f"    render 第 {attempt} 次未成功：{last_error}")
        raise StepFailed("render", last_error or "未知错误")

    def wait_render(self, canvas_id: str, job_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.cfg.render_timeout
        while time.monotonic() < deadline:
            state = self.api.json("GET", f"/api/node-canvases/{canvas_id}/final")
            entry = state.get("latest_render")
            if entry and entry["job_id"] == job_id and entry["status"] in ("succeeded", "failed", "canceled"):
                return entry
            time.sleep(self.cfg.poll_interval)
        raise TimeoutError(f"渲染超过 {self.cfg.render_timeout:.0f} 秒未完成")

    def download(self, entry: dict[str, Any], case_dir: Path) -> Optional[str]:
        if not entry.get("url"):
            return None
        response = self.api.request("GET", entry["url"])
        if response.status_code != 200:
            return None
        case_dir.mkdir(parents=True, exist_ok=True)
        path = case_dir / "final.mp4"
        path.write_bytes(response.content)
        return str(path)

    # ---- 一个用例 ----
    def run_case(self, story: dict[str, Any]) -> dict[str, Any]:
        record: dict[str, Any] = {
            "story_key": story["key"],
            "name": story.get("name", ""),
            "ok": False,
            "status": "failed",
            "failed_step": None,
            "error": None,
            "step_seconds": {},
            "retries": 0,
        }
        ctx = CaseCtx()
        started = time.monotonic()
        current = "split"
        try:
            self.prepare(story, record, ctx)
            if self.cfg.dry_quote:
                self.dry_quote(ctx, record)
                record["ok"], record["status"] = True, "ok"
                return record
            for step, kinds, price_kind in (
                ("keyframes", ["image"], "image"),
                ("videos", ["video"], "video"),
                ("voices", ["tts"], "tts"),
            ):
                current = step
                outcome = self.batch_step(ctx, step, kinds, price_kind)
                record["step_seconds"][step] = outcome["seconds"]
                record["retries"] += outcome["attempts"] - 1
            current = "render"
            # 渲染前最后核对一遍：进入成片的每个图片 / 视频产物都必须来自真实厂商
            self.verify_media(ctx, "render")
            rendered = self.render_step(ctx.canvas_id, self.cfg.run_dir / story["key"])
            record["step_seconds"]["render"] = rendered["seconds"]
            record["retries"] += rendered["attempts"] - 1
            qc = rendered.get("qc") or {}
            record["qc"] = {
                "status": qc.get("status"),
                "issues": qc.get("issues") or [],
                "checks": {k: {"status": v.get("status")} for k, v in (qc.get("checks") or {}).items()},
            }
            record["final_path"] = rendered["final_path"]
            record["ok"] = bool(rendered["final_path"]) and qc.get("status") != "failed"
            record["status"] = "ok" if record["ok"] else "failed"
            if not record["ok"]:
                record["failed_step"] = "render"
                record["error"] = "qc 未通过" if qc.get("status") == "failed" else "成片下载失败"
        except StepFailed as error:
            record["failed_step"], record["error"] = error.step, error.reason
        except BudgetStop as error:
            # 保留这个用例已经执行的全部记录（画布 id、花费、耗时、步骤），按失败计入分母
            record.update(status="budget_stopped", failed_step=error.step or current, error=error.reason)
            self.stop_reason = error.reason
        except MockDetected as error:
            record.update(status="invalid_mock", failed_step=error.step, error=error.reason)
            self.stop_reason = error.reason
        except (ApiError, httpx.HTTPError, TimeoutError) as error:
            record["failed_step"] = record.get("failed_step") or current
            record["error"] = str(error)[:300]
        finally:
            record["canvas_id"] = ctx.canvas_id or None
            record["duration_s"] = time.monotonic() - started
            record["tts_mocked"] = ctx.tts_mocked
            record["mock_media"] = ctx.mock_media
            if ctx.canvas_id and not self.cfg.dry_quote:
                try:
                    record["cost_yuan"] = self.final_cost(ctx)
                except (ApiError, httpx.HTTPError):
                    record["cost_yuan"] = self.case_cost(ctx)
                record["shot_latency_ms"] = shot_latencies_ms(ctx.shot_nodes, ctx.job_seconds)
            else:
                record.setdefault("cost_yuan", 0.0)
        return record

    def final_cost(self, ctx: CaseCtx) -> float:
        """用例总花费：以接口当前累计值为准；还没入账的部分补记进守卫。"""
        now = self.spent(ctx.canvas_id)
        if now > ctx.accounted:
            self.guard.record(now - ctx.accounted)
            ctx.accounted = now
        return self.case_cost(ctx)

    def prepare(self, story: dict[str, Any], record: dict[str, Any], ctx: CaseCtx) -> None:
        """新建画布 → 拆分（dry-quote 用本地拼的）→ 摆图 → 上传设定图。"""
        t0 = time.monotonic()
        try:
            canvas = self.api.json(
                "POST",
                "/api/node-canvases",
                json={
                    "title": f"bench · {story['key']} {story.get('name', '')}",
                    "aspect_ratio": story.get("aspect_ratio", "9:16"),
                },
            )
        except ApiError as error:
            raise StepFailed("split", f"建画布失败：{error.message}") from error
        canvas_id = ctx.canvas_id = canvas["id"]
        record["project_id"] = canvas.get("project_id")
        if self.cfg.dry_quote:
            split = local_split(story, self.cfg.max_shots)
        else:
            split = self.split(story, ctx, record)
        split["shots"] = split["shots"][: self.cfg.max_shots]
        record["shots"] = len(split["shots"])
        record["step_seconds"]["split"] = time.monotonic() - t0

        t1 = time.monotonic()
        graph, character_ids, ctx.shot_nodes = layout_graph(
            split,
            video_tier=self.cfg.video_tier,
            image_model=self.cfg.image_model,
            video_model=self.cfg.video_model,
        )
        ctx.node_kinds = {node["id"]: node["type"] for node in graph["nodes"]}
        try:
            self.api.json(
                "PUT",
                f"/api/node-canvases/{canvas_id}/graph",
                json={"graph": graph, "revision": canvas["revision"]},
            )
        except ApiError as error:
            raise StepFailed("characters", f"保存画布失败：{error.message}") from error
        uploaded = 0
        for name, node_id in character_ids.items():
            sheet = match_sheet(name, story)
            path = find_sheet(sheet["reference_image"], self.cfg.sheets_dirs) if sheet else None
            if path is None:
                continue
            mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
            try:
                self.api.json(
                    "POST",
                    f"/api/node-canvases/{canvas_id}/uploads",
                    data={"node_id": node_id},
                    files={"file": (path.name, path.read_bytes(), mime)},
                )
                uploaded += 1
            except ApiError as error:
                raise StepFailed("characters", f"上传角色设定图失败：{error.message}") from error
        record["characters"] = {
            "total": len(character_ids),
            "uploaded_sheets": uploaded,
            "generated": len(character_ids) - uploaded,
        }
        record["step_seconds"]["characters"] = time.monotonic() - t1

    def split(self, story: dict[str, Any], ctx: CaseCtx, record: dict[str, Any]) -> dict[str, Any]:
        """真实拆分：每个用例只调一次，不重试。

        调用前按“输入 × 2 + 输出 × 2（覆盖后端的 JSON 修复重试）× 3 倍余量”估算并过预算；总调用次数不超过
        --max-split-calls。无论成功、接口错误、超时还是其它异常，都按估算入账（LLM 免费额度内实际可能为 0）。
        """
        idea = build_idea(story, self.cfg.max_shots)
        shots = min(12, self.cfg.max_shots)
        estimate = estimate_split_cost(idea, shots, self.cfg.llm_input_price, self.cfg.llm_output_price)
        if self.split_calls >= self.cfg.max_split_calls:
            raise BudgetStop(f"拆分调用次数已达上限 {self.cfg.max_split_calls}（--max-split-calls）", "split")
        self.guard.check(estimate, label="split", multiplier=SPLIT_RETRY_HEADROOM)
        self.split_calls += 1
        record["split_cost_estimated"] = estimate
        try:
            split = self.api.json(
                "POST",
                f"/api/node-canvases/{ctx.canvas_id}/copilot/split",
                json={"idea": idea, "shots": shots},
            )
        except ApiError as error:
            raise StepFailed("split", f"拆分失败：{error.message}") from error
        finally:
            self.charge_split(ctx, estimate)
        if split.get("mocked"):
            if not self.cfg.allow_mock_media:
                raise MockDetected("split", "拆分结果来自 mock 对话模型，不是真实模型")
            ctx.mock_media = True
        return split

    def charge_split(self, ctx: CaseCtx, estimate: float) -> None:
        """拆分后入账：画布用量里读到的花费优先；LLM 用量不一定记在画布名下，所以至少按估算计入。读不到用量也按估算计。"""
        try:
            delta = self.sync_spend(ctx)
        except (ApiError, httpx.HTTPError):
            delta = 0.0
        extra = max(0.0, estimate - delta)
        ctx.split_extra += extra
        self.guard.record(extra)

    def dry_quote(self, ctx: CaseCtx, record: dict[str, Any]) -> None:
        """只报价：关键帧 + 视频一起报（视频用未生成上游的占位估价），再报配音。之后删掉这张临时画布。"""
        canvas_id = ctx.canvas_id
        media = self.api.json(
            "POST",
            f"/api/node-canvases/{canvas_id}/batch-quote",
            json={"scope": "all", "skip_succeeded": True},
        )
        try:
            voice = self.api.json(
                "POST", f"/api/node-canvases/{canvas_id}/batch-quote", json={"scope": "all", "kinds": ["tts"]}
            )
        except ApiError:
            voice = {"estimated_total": 0, "nodes": []}
        record["quote"] = {
            "media": float(media.get("estimated_total") or 0),
            "voice": float(voice.get("estimated_total") or 0),
            "unpriced_nodes": quote_unpriced(media) + quote_unpriced(voice),
            "blocking": [
                n.get("reason") for n in media.get("unavailable_nodes", []) if n.get("blocks_batch")
            ],
        }
        if record["quote"]["blocking"]:
            record["error"] = "；".join(record["quote"]["blocking"])[:300]
        record["cost_yuan"] = record["quote"]["media"] + record["quote"]["voice"]
        try:
            self.api.json("DELETE", f"/api/node-canvases/{canvas_id}", ok=(200, 204))
        except (ApiError, httpx.HTTPError):
            pass

    def ensure_no_real_deployments(self) -> None:
        """--allow-mock-media 开跑前：不能有任何已启用的真实图片 / 视频 / 对话部署，否则会真实调用厂商。"""
        try:
            rows = self.api.json("GET", "/api/model-deployments")
        except ApiError as error:
            raise BudgetStop(
                f"无法读取模型部署列表（需要管理员账号）：{error.message}，--allow-mock-media 拒绝启动",
                "preflight",
            ) from error
        real = sorted(
            f"{row['slug']}({row['modality']})"
            for row in rows
            if row.get("enabled") and row.get("modality") in ("image", "video", "chat")
        )
        if real:
            raise BudgetStop(
                "--allow-mock-media 只能在没有真实模型的环境用，但这些部署已启用并可能产生真实花费："
                + "、".join(real)
                + "。请先停用它们，或去掉 --allow-mock-media 走真实跑流程",
                "preflight",
            )

    def check_prices(self, image_model: str, video_model: str, tier: str) -> None:
        """真实跑开跑前：图片 / 视频模型的报价口径与 worker 落账口径必须一致（服务端函数核对）。"""
        try:
            data = self.api.json(
                "GET",
                "/api/eval/price-consistency",
                params={"image_model": image_model, "video_model": video_model, "tier": tier},
            )
        except ApiError as error:
            raise BudgetStop(
                f"无法做价格一致性检查（需要管理员账号）：{error.message}", "preflight"
            ) from error
        if not data.get("ok"):
            raise BudgetStop(
                "价格不一致，拒绝启动：" + "；".join(data.get("problems") or ["未知"]), "preflight"
            )

    def preflight_budget(self, limit: float) -> None:
        """开跑前核对服务端每日云端花费上限（真正的兜底）；不合格抛 BudgetStop。"""
        canvas = self.api.json("POST", "/api/node-canvases", json={"title": "bench · preflight"})
        try:
            runs = self.api.json("GET", f"/api/node-canvases/{canvas['id']}/runs")
        finally:
            try:
                self.api.json("DELETE", f"/api/node-canvases/{canvas['id']}", ok=(200, 204))
            except (ApiError, httpx.HTTPError):
                pass
        reason = check_daily_cap(runs.get("budget") or {}, limit)
        if reason:
            raise BudgetStop(reason, "preflight")


def batch_failure_reason(detail: dict[str, Any]) -> str:
    """批次失败 / 取消 → 第一个失败节点的原因。"""
    for _node_id, state in (detail.get("nodes") or {}).items():
        if state.get("status") in ("failed", "blocked", "canceled"):
            return f"{state['status']}: {state.get('reason') or ''}"[:300]
    return f"批次状态 {detail.get('status')}"


# ── 入口 ──────────────────────────────────────────────────────────────────


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="画布全流程端到端基准（只走 HTTP API）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--stories", default="10", help="前 N 个镜头数 >= --min-shots 的故事，或逗号分隔的 key"
    )
    parser.add_argument("--min-shots", type=int, default=5)
    parser.add_argument("--max-shots", type=int, default=6)
    parser.add_argument("--budget-yuan", type=float, default=None, help="总预算上限（元）；真实跑必填")
    parser.add_argument("--dry-quote", action="store_true")
    parser.add_argument("--allow-unpriced", action="store_true")
    parser.add_argument("--allow-mock-media", action="store_true")
    parser.add_argument("--worker-max-attempts", type=int, default=3)
    parser.add_argument("--max-split-calls", type=int, default=None)
    parser.add_argument("--llm-input-price", type=float, default=4.0)
    parser.add_argument("--llm-output-price", type=float, default=16.0)
    parser.add_argument("--resolution", default="480p", choices=sorted(TIER_BY_RESOLUTION))
    parser.add_argument("--video-model", default="")
    parser.add_argument("--image-model", default="")
    parser.add_argument("--sheets-dir", action="append", default=[])
    parser.add_argument("--step-timeout", type=float, default=900)
    parser.add_argument("--render-timeout", type=float, default=300)
    parser.add_argument("--settle-timeout", type=float, default=300)
    parser.add_argument("--poll-interval", type=float, default=2)
    parser.add_argument("--out-dir", default="bench-results")
    parser.add_argument("--no-eval-run", action="store_true")
    return parser.parse_args(argv)


def write_eval_run(
    api: Api, case: dict[str, Any], datasets: dict[str, dict[str, Any]], args: argparse.Namespace
) -> dict[str, Any]:
    dataset = datasets.get(case["story_key"])
    if not dataset:
        return {"story_key": case["story_key"], "error": "基准数据集里没有这个故事"}
    samples = build_eval_samples(case)
    # 样本数少于基准镜头数就是局部运行：显式声明，服务端据此区分 input_hash 并排除出路由统计
    partial = len(samples) if len(samples) != dataset["shot_count"] else None
    try:
        row = api.json(
            "POST",
            "/api/eval/runs",
            json={
                "dataset_id": dataset["id"],
                "provider": "canvas-e2e-bench-mock" if args.allow_mock_media else "canvas-e2e-bench",
                "deployment": args.video_model or "default-route",
                "model": f"canvas-e2e-{args.resolution}",
                "project_id": case.get("project_id"),
                "samples": samples,
                "partial_shots": partial,
            },
        )
    except ApiError as error:
        return {"story_key": case["story_key"], "error": error.message}
    return {"story_key": case["story_key"], "id": row["id"]}


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    username = os.getenv("BENCH_USERNAME") or os.getenv("ADMIN_USERNAME") or ""
    password = os.getenv("BENCH_PASSWORD") or os.getenv("ADMIN_PASSWORD") or ""
    if not username or not password:
        print("缺少 BENCH_USERNAME / BENCH_PASSWORD 环境变量", file=sys.stderr)
        return 2
    # 除 --dry-quote 外一律按“可能花钱”对待：--allow-mock-media 不免除预算和服务端上限检查
    real_run = not args.dry_quote
    if real_run and args.budget_yuan is None:
        print("必须指定 --budget-yuan（总预算上限）；--allow-mock-media 也不例外", file=sys.stderr)
        return 2
    data = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
    try:
        stories = select_stories(data["stories"], args.stories, args.min_shots)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    if not stories:
        print("没有符合条件的故事", file=sys.stderr)
        return 2
    try:
        api = Api(args.base_url, username, password, forbid_paid=args.dry_quote)
    except (PermissionError, httpx.HTTPError) as error:
        print(f"无法连接后端：{error}", file=sys.stderr)
        return 2

    if args.allow_mock_media:
        print("=" * 70, flush=True)
        print("警告：--allow-mock-media 已开启，图片 / 视频 / 拆分允许来自 mock。", flush=True)
        print("本次结果不是真实基准，不能用于阶段 1 退出标准判定。", flush=True)
        print("=" * 70, flush=True)

    started_at = datetime.now()
    run_dir = Path(args.out_dir) / started_at.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg = Settings(
        max_shots=args.max_shots,
        video_tier=TIER_BY_RESOLUTION[args.resolution],
        image_model=args.image_model,
        video_model=args.video_model,
        step_timeout=args.step_timeout,
        render_timeout=args.render_timeout,
        settle_timeout=args.settle_timeout,
        poll_interval=args.poll_interval,
        sheets_dirs=[Path(item) for item in args.sheets_dir] or default_sheet_dirs(),
        run_dir=run_dir,
        dry_quote=args.dry_quote,
        allow_mock_media=args.allow_mock_media,
        max_split_calls=args.max_split_calls or len(stories),
        llm_input_price=args.llm_input_price,
        llm_output_price=args.llm_output_price,
    )
    guard = BudgetGuard(
        limit=args.budget_yuan,
        # mock 联调时图片没有价格，放行；真实跑默认拒绝未配置价格的节点
        allow_unpriced=args.allow_unpriced or args.allow_mock_media,
        attempts=args.worker_max_attempts,
    )
    runner = Runner(api, cfg, guard)
    if real_run:
        try:
            runner.preflight_budget(args.budget_yuan)
            if args.allow_mock_media:
                runner.ensure_no_real_deployments()
            else:
                runner.check_prices(args.image_model, args.video_model, cfg.video_tier)
        except BudgetStop as error:
            print(error.reason, file=sys.stderr)
            return 2
    print(f"基准开始：{len(stories)} 个用例，每例最多 {args.max_shots} 镜头，输出 {run_dir}", flush=True)

    cases: list[dict[str, Any]] = []
    stopped_reason = ""
    for index, story in enumerate(stories, 1):
        if stopped_reason:
            cases.append(
                {"story_key": story["key"], "name": story.get("name", ""), "ok": False, "status": "not_run"}
            )
            continue
        print(f"[{index}/{len(stories)}] {story['key']} {story.get('name', '')}", flush=True)
        case = runner.run_case(story)
        cases.append(case)
        if runner.stop_reason:
            stopped_reason = runner.stop_reason
            print(f"  基准停止：{stopped_reason}", flush=True)
        print(
            f"  {STATUS_LABEL.get(case['status'], case['status'])}"
            f"{' @' + str(case.get('failed_step')) if not case['ok'] else ''} "
            f"耗时 {case.get('duration_s', 0):.1f}s 花费 ¥{case.get('cost_yuan') or 0:.4f} "
            f"qc={(case.get('qc') or {}).get('status')}",
            flush=True,
        )

    eval_runs: list[dict[str, Any]] = []
    if not args.no_eval_run and not args.dry_quote:
        try:
            datasets = {item["key"]: item for item in api.json("GET", "/api/eval/datasets")}
        except (ApiError, httpx.HTTPError):
            datasets = {}
        for case in cases:
            if case.get("status") != "not_run":
                eval_runs.append(write_eval_run(api, case, datasets, args))

    report: dict[str, Any] = {
        "started_at": started_at.isoformat(timespec="seconds"),
        "base_url": args.base_url,
        "video_tier": cfg.video_tier,
        "args": {
            "stories": args.stories,
            "max_shots": args.max_shots,
            "resolution": args.resolution,
            "video_model": args.video_model,
            "image_model": args.image_model,
            "budget_yuan": args.budget_yuan,
            "allow_mock_media": args.allow_mock_media,
        },
        "budget": {
            "limit": args.budget_yuan,
            "spent": guard.spent,
            "unpriced_nodes": guard.unpriced_nodes,
            "stopped_reason": stopped_reason or None,
        },
        "cases": cases,
        "summary": summarize(cases, mock_media=args.allow_mock_media),
        "eval_runs": eval_runs,
    }
    if args.dry_quote:
        quoted = [case["quote"] for case in cases if case.get("quote")]
        report["dry_quote"] = {
            "estimated_total": sum(q["media"] + q["voice"] for q in quoted),
            "media_total": sum(q["media"] for q in quoted),
            "voice_total": sum(q["voice"] for q in quoted),
            "unpriced_nodes": sum(q["unpriced_nodes"] for q in quoted),
        }
    (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    print(f"报告：{run_dir / 'report.md'}", flush=True)
    if stopped_reason:
        return 3
    return 0 if report["summary"]["meets_exit_criteria"] or args.dry_quote else 1


if __name__ == "__main__":
    sys.exit(main())
