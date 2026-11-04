"""画布端到端基准脚本的单测：预算判断、故事 / 画布构造、汇总统计、报告生成、EvalRun 样本、
dry-quote 不触碰收费接口、异常路径结算。HTTP 用 httpx.MockTransport 假后端，不起服务器。

脚本按路径加载（scripts/ 不是包）。另含 Mock 对话模型（拆分兜底）与 EvalRun 局部运行的测试。
"""

import asyncio
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "bench_canvas_e2e.py"
spec = importlib.util.spec_from_file_location("bench_canvas_e2e", SCRIPT)
bench = importlib.util.module_from_spec(spec)
sys.modules["bench_canvas_e2e"] = bench  # dataclass 需要能按模块名找到自己
spec.loader.exec_module(bench)


def _story(key="s1", shots=6, characters=None):
    characters = characters or [
        {
            "key": "a",
            "name": "甲",
            "name_en": "Alpha",
            "prompt": "红衣少女",
            "reference_image": "benchmarks/s1/a.jpeg",
        },
        {"key": "b", "name": "乙", "name_en": "Beta", "prompt": "黑衣少年", "reference_image": None},
        {"key": "c", "name": "丙", "name_en": "Gamma", "prompt": "老者", "reference_image": None},
    ]
    return {
        "key": key,
        "name": "测试故事",
        "aspect_ratio": "9:16",
        "characters": characters,
        "shots": [
            {
                "key": f"shot-{i}",
                "plot": f"情节{i}",
                "description": f"画面{i}",
                "setting": "白天",
                "camera": "中景",
                "characters": ["a"] if i % 2 else ["a", "b"],
            }
            for i in range(1, shots + 1)
        ],
    }


# ── 预算 ──────────────────────────────────────────────────────────────────


def test_budget_guard_multiplies_quote_by_worker_attempts_only():
    guard = bench.BudgetGuard(limit=10, attempts=3)
    guard.check(3.3, kind="image", label="i")  # 3.3 × 3 = 9.9 <= 10，不再有图片容差倍数
    with pytest.raises(bench.BudgetStop, match="超出总预算"):
        guard.check(3.4, kind="image", label="i")  # 10.2 > 10
    guard.record(2)
    with pytest.raises(bench.BudgetStop):
        guard.check(3.0, kind="video", label="v")  # 2 + 9 > 10
    guard.check(2.6, kind="video")  # 2 + 7.8 <= 10
    assert guard.remaining() == pytest.approx(8)


def test_budget_guard_unlimited_and_negative_actuals_ignored():
    guard = bench.BudgetGuard(limit=None)
    guard.check(1e9)
    guard.record(-5)
    assert guard.spent == 0 and guard.remaining() is None


def test_budget_guard_rejects_unpriced_by_default_and_counts_when_allowed():
    default = bench.BudgetGuard(limit=100)
    with pytest.raises(bench.BudgetStop, match="未配置价格"):
        default.check(0, unpriced=1, label="videos")
    # 预算为 0 时价格未知的节点也不能放行
    zero = bench.BudgetGuard(limit=0)
    with pytest.raises(bench.BudgetStop):
        zero.check(0, unpriced=2, label="keyframes", kind="image")
    lenient = bench.BudgetGuard(limit=100, allow_unpriced=True)
    lenient.check(1, unpriced=2)
    lenient.check(1, unpriced=1)
    assert lenient.unpriced_nodes == 3


def test_guard_explicit_multiplier_for_split_headroom():
    guard = bench.BudgetGuard(limit=0.05)
    guard.check(0.01, label="split", multiplier=3)
    with pytest.raises(bench.BudgetStop):
        guard.check(0.02, label="split", multiplier=3)


def test_estimate_split_cost_covers_json_repair_retry():
    small = bench.estimate_split_cost("a" * 100, 3, 4.0, 16.0)
    large = bench.estimate_split_cost("a" * 2000, 12, 4.0, 16.0)
    assert 0 < small < large
    # 输入和输出都按 2 倍（后端会带着上一轮输出再请求一次）：((450+100)*4 + (300+780)*16) * 2 / 1e6
    assert small == pytest.approx(38960 / 1e6)


def test_check_daily_cap_is_the_hard_backstop():
    # 上限与 预算 + 今日已花 相等：通过
    assert bench.check_daily_cap({"cap": 25.0, "spent": 5.0, "in_flight": 0.0}, 20) is None
    # 上限更小：服务端更早拒绝，允许
    assert bench.check_daily_cap({"cap": 10.0, "spent": 5.0, "in_flight": 0.0}, 20) is None
    # 上限更大：拒绝并给出应设置的值
    reason = bench.check_daily_cap({"cap": 100.0, "spent": 5.0, "in_flight": 1.0}, 20)
    assert "DAILY_BUDGET_CNY" in reason and "26.00" in reason
    # 不限：拒绝
    reason = bench.check_daily_cap({"cap": None, "spent": 0, "in_flight": 0}, 20)
    assert "不限" in reason and "20.00" in reason


def test_quote_unpriced_only_counts_runnable_unknown_price_nodes():
    quote = {
        "nodes": [
            {"status": "pending", "estimated_cost": None, "blockers": [], "waits_upstream": False},
            {"status": "pending", "estimated_cost": None, "blockers": ["缺提示词"], "waits_upstream": False},
            {"status": "pending", "estimated_cost": None, "blockers": [], "waits_upstream": True},
            {"status": "skipped", "estimated_cost": 0.0, "blockers": [], "waits_upstream": False},
            {"status": "pending", "estimated_cost": 0.4, "blockers": [], "waits_upstream": False},
        ]
    }
    assert bench.quote_unpriced(quote) == 1


# ── 故事选择 / 想法 / 画布 ────────────────────────────────────────────────


def test_select_stories_by_count_skips_short_stories_and_by_key():
    stories = [_story("a", 3), _story("b", 6), _story("c", 9), _story("d", 5)]
    assert [s["key"] for s in bench.select_stories(stories, "2")] == ["b", "c"]
    assert [s["key"] for s in bench.select_stories(stories, "10", min_shots=3)] == ["a", "b", "c", "d"]
    assert [s["key"] for s in bench.select_stories(stories, "d, a")] == ["d", "a"]
    with pytest.raises(ValueError, match="x"):
        bench.select_stories(stories, "a,x")


def test_story_characters_follow_truncated_shots_in_first_appearance_order():
    story = _story(shots=6)
    assert [c["name"] for c in bench.story_characters(story, 1)] == ["甲"]
    assert [c["name"] for c in bench.story_characters(story, 2)] == ["甲", "乙"]
    assert len(bench.story_characters(story, 6, limit=1)) == 1


def test_build_idea_lists_characters_and_truncated_structured_shots():
    idea = bench.build_idea(_story(shots=6), 2)
    lines = idea.splitlines()
    assert lines[0] == "故事：测试故事"
    assert lines[1:3] == ["角色：甲：红衣少女", "角色：乙：黑衣少年"]
    shots = [line for line in lines if line.startswith("镜头")]
    assert len(shots) == 2
    assert shots[1] == "镜头2：情节2｜画面：画面2｜环境：白天｜镜头：中景｜出镜：甲、乙"


def test_local_split_uses_dataset_text_without_any_model():
    split = bench.local_split(_story(shots=6), 3)
    assert [c["name"] for c in split["characters"]] == ["甲", "乙"]
    assert len(split["shots"]) == 3
    assert split["shots"][1]["characters"] == ["甲", "乙"]
    assert split["shots"][0]["lines"] == [{"speaker": "旁白", "text": "情节1"}]
    assert "画面1" in split["shots"][0]["prompt"] and split["mocked"] is False
    graph, _, shot_nodes = bench.layout_graph(split, video_tier="preview")
    assert len(shot_nodes) == 3 and len([n for n in graph["nodes"] if n["type"] == "video"]) == 3


def test_match_sheet_prefers_exact_then_english_then_substring_and_needs_image():
    story = _story()
    assert bench.match_sheet("甲", story)["key"] == "a"
    assert bench.match_sheet("alpha", story)["key"] == "a"
    assert bench.match_sheet("小甲", story)["key"] == "a"
    assert bench.match_sheet("乙", story) is None
    assert bench.match_sheet("无关", story) is None


def test_layout_graph_wires_references_first_frames_and_voices():
    split = {
        "characters": [{"name": "甲", "description": "红衣"}],
        "shots": [
            {
                "title": "开场",
                "prompt": "p1",
                "characters": ["甲"],
                "duration_s": 4,
                "lines": [{"speaker": "旁白", "text": "你好"}],
            },
            {
                "title": "",
                "prompt": "p2",
                "characters": ["不存在"],
                "duration_s": 5,
                "lines": [{"speaker": "", "text": "  "}],
            },
        ],
    }
    graph, ids, shot_nodes = bench.layout_graph(
        split, video_tier="preview", image_model="img", video_model="vid"
    )
    by_type: dict[str, list] = {}
    for node in graph["nodes"]:
        by_type.setdefault(node["type"], []).append(node)
    assert len(by_type["image"]) == 3 and len(by_type["video"]) == 2 and len(by_type["audio"]) == 1
    character = next(n for n in by_type["image"] if n["data"].get("role") == "character")
    assert ids == {"甲": character["id"]} and character["data"]["character"] == "甲"
    assert all(v["data"]["tier"] == "preview" and v["data"]["model"] == "vid" for v in by_type["video"])
    handles = sorted(edge["targetHandle"] for edge in graph["edges"])
    assert handles == ["first_frame", "first_frame", "reference", "voice"]
    assert by_type["video"][1]["data"]["label"] == "2 · 镜头 2"
    node_ids = {n["id"] for n in graph["nodes"]}
    assert all(e["source"] in node_ids and e["target"] in node_ids for e in graph["edges"])
    assert len({e["id"] for e in graph["edges"]}) == len(graph["edges"])
    # 每个镜头记下自己的关键帧 / 视频 / 配音节点，供统计镜头级耗时
    assert shot_nodes[0]["video"] == by_type["video"][0]["id"] and len(shot_nodes[0]["audios"]) == 1
    assert shot_nodes[1]["audios"] == []


# ── 任务耗时 / 真实性 ─────────────────────────────────────────────────────


def test_job_seconds_uses_started_at_then_created_at_and_never_invents():
    finished = "2026-11-05T10:00:42.500000+00:00"
    started = {"started_at": "2026-11-05T10:00:00+00:00", "created_at": "2026-11-05T09:59:00+00:00"}
    assert bench.job_seconds({**started, "finished_at": finished}) == pytest.approx(42.5)
    # 图片 / 配音 worker 不写 started_at：用 created_at（含排队）
    assert bench.job_seconds(
        {"created_at": "2026-11-05T10:00:40+00:00", "finished_at": finished}
    ) == pytest.approx(2.5)
    assert bench.job_seconds({"started_at": None, "created_at": None, "finished_at": finished}) is None
    assert bench.job_seconds({**started, "finished_at": None}) is None
    assert bench.job_seconds({"started_at": finished, "finished_at": started["started_at"]}) is None


def test_job_is_mock_detects_every_marker():
    assert bench.job_is_mock({"result": {"mocked": True}})
    assert bench.job_is_mock({"provider": "mock_image"})
    assert bench.job_is_mock({"provider": "volcano", "model": "mock-video"})
    assert bench.job_is_mock({"params": {"routing_snapshot": {"provider": "mock"}}})
    assert bench.job_is_mock({"params": {"deployment_name": "legacy-mock-image"}})
    assert not bench.job_is_mock(
        {"provider": "volcano_video", "model": "seedance", "result": {"mocked": False}, "params": {}}
    )


def test_shot_latencies_use_real_job_times_and_never_invent_missing_ones():
    shots = [
        {"keyframe": "k1", "video": "v1", "audios": ["a1", "a2"]},
        {"keyframe": "k2", "video": "v2", "audios": []},
        {"keyframe": "k3", "video": "v3", "audios": ["a3"]},
    ]
    seconds = {"k1": 2.0, "v1": 60.0, "a1": 1.0, "k2": 3.0, "v2": 90.0, "k3": 1.0, "a3": 1.0}
    # 镜头 1 = 2 + 60 + 1（a2 没有时间就不算）；镜头 2 = 93；镜头 3 缺视频耗时 → None
    assert bench.shot_latencies_ms(shots, seconds) == [63000, 93000, None]


# ── 统计 / 报告 ───────────────────────────────────────────────────────────


def _case(key, ok=True, seconds=100.0, cost=1.0, qc="passed", step=None, **extra):
    case = {
        "story_key": key,
        "name": key,
        "ok": ok,
        "status": "ok" if ok else "failed",
        "duration_s": seconds,
        "cost_yuan": cost,
        "shots": 3,
        "retries": 0,
        "step_seconds": {"keyframes": 10.0, "videos": seconds - 20, "render": 10.0},
        "qc": {"status": qc, "issues": [], "checks": {}} if ok else None,
        "final_path": f"/x/{key}.mp4" if ok else None,
        "failed_step": step,
        "error": None if ok else "boom",
    }
    case.update(extra)
    return case


def test_percentile_matches_linear_interpolation():
    assert bench.percentile([], 0.5) is None
    assert bench.percentile([5], 0.95) == 5
    assert bench.percentile([1, 2, 3, 4], 0.5) == pytest.approx(2.5)
    assert bench.percentile([10, 20, 30, 40, 50], 0.95) == pytest.approx(48)


def test_summarize_rates_costs_and_exit_criteria():
    cases = [_case(f"s{i}", cost=float(i + 1), seconds=100.0 + i) for i in range(9)]
    cases.append(_case("bad", ok=False, cost=0.7, step="videos"))
    summary = bench.summarize(cases)
    assert (
        summary["succeeded"] == 9 and summary["failed"] == 1 and summary["success_rate"] == pytest.approx(0.9)
    )
    assert summary["exported"] == 9 and summary["qc_pass_rate"] == 1.0
    assert summary["cost_yuan"]["avg"] == pytest.approx(5.0)
    assert summary["cost_yuan"]["total_all_cases"] == pytest.approx(45.7)
    assert summary["duration_s"]["p95"] == pytest.approx(107.6)
    assert summary["failed_steps"] == {"videos": 1}
    assert summary["step_seconds_avg"]["keyframes"] == pytest.approx(10.0)
    assert summary["sufficient_scale"] is True and summary["meets_exit_criteria"] is True


def test_qc_failed_exports_stay_in_the_qc_denominator_and_block_the_criteria():
    # 10 个用例：9 个 qc 通过，1 个导出了文件但 qc failed（ok=False）。成功率 90%，但不能达标
    cases = [_case(f"s{i}") for i in range(9)]
    cases.append(_case("q", ok=False, step="render", qc="failed", final_path="/x/q.mp4"))
    cases[-1]["qc"] = {"status": "failed", "issues": [], "checks": {}}
    summary = bench.summarize(cases)
    assert summary["success_rate"] == pytest.approx(0.9)
    assert summary["exported"] == 10 and summary["qc_passed"] == 9 and summary["qc_failed"] == 1
    assert summary["qc_pass_rate"] == pytest.approx(0.9)  # 分母含失败的那个
    assert summary["meets_exit_criteria"] is False


def test_fewer_than_ten_cases_never_meet_the_exit_criteria():
    cases = [_case(f"s{i}") for i in range(3)]
    summary = bench.summarize(cases)
    assert summary["success_rate"] == 1.0 and summary["sufficient_scale"] is False
    assert summary["meets_exit_criteria"] is False
    assert "规模不足以判定退出标准" in bench.verdict_text(summary)
    assert bench.summarize([_case("only")])["meets_exit_criteria"] is False


def test_budget_stopped_case_counts_as_failure_and_keeps_its_spend():
    stopped = _case("b", ok=False, step="videos", cost=2.5, status="budget_stopped", canvas_id="c-b")
    cases = [_case(f"s{i}") for i in range(9)] + [
        stopped,
        {"story_key": "n", "ok": False, "status": "not_run"},
    ]
    summary = bench.summarize(cases)
    assert summary["ran"] == 10 and summary["not_run"] == 1 and summary["failed"] == 1
    assert summary["success_rate"] == pytest.approx(0.9)
    assert summary["cost_yuan"]["total_all_cases"] == pytest.approx(11.5)  # 含被预算拦下用例的花费
    assert summary["statuses"]["budget_stopped"] == 1 and summary["meets_exit_criteria"] is False


def test_invalid_mock_and_mock_media_block_the_criteria():
    cases = [_case(f"s{i}") for i in range(10)]
    assert bench.summarize(cases)["meets_exit_criteria"] is True
    assert bench.summarize(cases, mock_media=True)["meets_exit_criteria"] is False
    cases[0]["mock_media"] = True
    assert bench.summarize(cases)["meets_exit_criteria"] is False
    mocked = [_case(f"s{i}") for i in range(9)] + [
        _case("m", ok=False, step="keyframes", status="invalid_mock")
    ]
    assert bench.summarize(mocked)["meets_exit_criteria"] is False


def test_render_markdown_contains_summary_cases_problems_and_tts_note():
    cases = [
        _case("s1", qc="warning", tts_mocked=True),
        _case("s2", ok=False, step="videos", error="节点失败", retries=1),
    ]
    cases[0]["qc"]["issues"] = [{"code": "subtitle_overflow", "level": "warning", "message": "字幕太长"}]
    report = {
        "started_at": "2026-11-05T10:00:00",
        "base_url": "http://x",
        "video_tier": "preview",
        "args": {"resolution": "480p", "max_shots": 3, "video_model": "", "image_model": ""},
        "budget": {"limit": 10.0, "spent": 1.5, "unpriced_nodes": 2, "stopped_reason": None},
        "cases": cases,
        "summary": bench.summarize(cases),
        "eval_runs": [{"story_key": "s1", "id": "run-1"}],
    }
    text = bench.render_markdown(report)
    assert "成功率：1/2 = 50%" in text
    assert "| s1 s1 | 成功 |" in text and "| s2 s2 | 失败 | videos |" in text
    assert "subtitle_overflow" in text and "节点失败（重试 1 次）" in text
    assert "2 个节点未配置价格" in text and "EvalRun s1：run-1" in text
    assert "配音为模拟（无真实 TTS key）" in text
    assert "规模不足以判定退出标准" in text
    json.dumps(report, ensure_ascii=False)


def test_render_markdown_dry_quote_budget_stop_and_mock_warning():
    cases = [{"story_key": "a", "name": "a", "ok": True, "status": "ok", "cost_yuan": 3.0, "shots": 3}]
    report = {
        "started_at": "t",
        "base_url": "u",
        "video_tier": "preview",
        "args": {"resolution": "480p", "max_shots": 3},
        "budget": {"limit": None, "spent": 0, "unpriced_nodes": 0, "stopped_reason": "videos 报价超出"},
        "cases": cases,
        "summary": bench.summarize(cases, mock_media=True),
        "dry_quote": {"estimated_total": 3.0, "media_total": 2.5, "voice_total": 0.5, "unpriced_nodes": 0},
    }
    text = bench.render_markdown(report)
    assert "只报价" in text and "总预估：¥3.00" in text and "基准被停止" in text
    assert "结果不能用于基准判定" in text


def test_build_eval_samples_uses_real_per_shot_latency_and_omits_unknown():
    case = _case("s1", seconds=60.0, cost=3.0, qc="warning", shot_latency_ms=[63000, None, 93000])
    case["qc"]["checks"] = {"av_duration_diff_ms": {"status": "passed"}}
    samples = bench.build_eval_samples(case)
    assert [s.get("latency_ms") for s in samples] == [63000, None, 93000]
    assert "latency_ms" not in samples[1]  # 没测到就不填，不拿平均值凑
    assert samples[0]["cost_cny"] == pytest.approx(1.0) and samples[0]["technical_quality"] == 0.7
    assert samples[0]["av_sync"] == 1.0 and "character_consistency" not in samples[0]
    failed = bench.build_eval_samples(_case("s2", ok=False, step="render", cost=None, shots=0))
    assert len(failed) == 1 and failed[0]["success"] is False and failed[0]["cost_cny"] is None
    assert failed[0]["failure_reasons"] == ["render: boom"] and "latency_ms" not in failed[0]


def test_batch_failure_reason_picks_first_failed_node():
    detail = {
        "status": "failed",
        "nodes": {"a": {"status": "succeeded"}, "b": {"status": "failed", "reason": "超时"}},
    }
    assert bench.batch_failure_reason(detail) == "failed: 超时"
    assert bench.batch_failure_reason({"status": "canceled", "nodes": {}}) == "批次状态 canceled"


def test_error_message_prefers_structured_message():
    assert (
        bench.error_message(httpx.Response(422, json={"error": {"message": "不行"}, "detail": "x"})) == "不行"
    )
    assert bench.error_message(httpx.Response(400, json={"detail": "细节"})) == "细节"
    assert bench.error_message(httpx.Response(502, text="bad gateway")).startswith("HTTP 502")


def test_parse_args_defaults_are_safe():
    args = bench.parse_args([])
    assert args.resolution == "480p" and args.max_shots == 6 and args.stories == "10"
    assert args.allow_unpriced is False and args.allow_mock_media is False and args.worker_max_attempts == 3
    assert bench.TIER_BY_RESOLUTION[args.resolution] == "preview"
    assert bench.TIER_BY_RESOLUTION["720p"] == "final"


# ── 假后端：dry-quote 不碰收费接口 / 异常路径结算 ─────────────────────────


class FakeBackend:
    """记录所有请求；handlers 按 (method, 路径正则) 定制响应，默认给一个能跑通建图 / 报价的后端。"""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.handlers: list[tuple[str, str, object]] = []

    def on(self, method, pattern, response):
        self.handlers.append((method, pattern, response))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        for method, pattern, response in self.handlers:
            if method == request.method and re.search(pattern, path):
                if isinstance(response, Exception):
                    raise response
                return response(request) if callable(response) else httpx.Response(200, json=response)
        if path == "/api/auth/login":
            return httpx.Response(200, json={"access_token": "t"})
        if request.method == "POST" and path == "/api/node-canvases":
            return httpx.Response(201, json={"id": "c1", "revision": 1, "project_id": "p1"})
        if request.method == "PUT" and path.endswith("/graph"):
            return httpx.Response(200, json={"revision": 2})
        if request.method == "POST" and path.endswith("/batch-quote"):
            body = json.loads(request.content)
            total = 0.2 if body.get("kinds") == ["tts"] else 1.5
            return httpx.Response(200, json={"estimated_total": total, "nodes": [], "unavailable_nodes": []})
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(404, json={"detail": f"unhandled {request.method} {path}"})

    def paid_calls(self):
        return [
            (m, p) for m, p in self.calls if m != "GET" and any(re.search(x, p) for x in bench.PAID_PATTERNS)
        ]


def _cfg(tmp_path, **overrides):
    values = dict(
        max_shots=3,
        video_tier="preview",
        image_model="",
        video_model="",
        step_timeout=0.2,
        render_timeout=0.2,
        settle_timeout=0.1,
        poll_interval=0.01,
        sheets_dirs=[],
        run_dir=tmp_path,
    )
    values.update(overrides)
    return bench.Settings(**values)


def _api(backend, **kwargs):
    return bench.Api("http://fake", "u", "p", transport=httpx.MockTransport(backend), **kwargs)


def test_api_blocks_paid_endpoints_in_dry_run_before_sending_anything():
    backend = FakeBackend()
    api = _api(backend, forbid_paid=True)
    for method, path in [
        ("POST", "/api/node-canvases/c1/copilot/split"),
        ("POST", "/api/node-canvases/c1/batch-runs"),
        ("POST", "/api/node-canvases/c1/batch-runs/b1/cancel"),
        ("POST", "/api/node-canvases/c1/nodes/n1/run"),
        ("POST", "/api/node-canvases/c1/final/render"),
        ("POST", "/api/eval/runs"),
    ]:
        with pytest.raises(bench.DryRunViolation):
            api.json(method, path, json={})
    assert backend.calls == [("POST", "/api/auth/login")]  # 除登录外一个请求都没发出
    # 只读 / 免费接口放行（批量报价不是批量运行）
    api.json("POST", "/api/node-canvases/c1/batch-quote", json={})


def test_dry_quote_case_builds_canvas_locally_and_never_calls_paid_endpoints(tmp_path):
    backend = FakeBackend()
    runner = bench.Runner(
        _api(backend, forbid_paid=True), _cfg(tmp_path, dry_quote=True), bench.BudgetGuard(limit=0)
    )
    case = runner.run_case(_story(shots=6))
    assert case["ok"] is True and case["shots"] == 3
    assert case["quote"]["media"] == 1.5 and case["quote"]["voice"] == 0.2
    assert backend.paid_calls() == []
    methods = {(m, re.sub(r"c1", "{id}", p)) for m, p in backend.calls}
    assert ("PUT", "/api/node-canvases/{id}/graph") in methods  # 经保存画布接口建图
    assert not any("/copilot/split" in p for _, p in backend.calls)


def _running_batch(batch_id="b1"):
    return {"id": batch_id, "status": "running", "nodes": {"n": {"status": "running", "job_id": "j1"}}}


def _step_ctx():
    ctx = bench.CaseCtx(canvas_id="c1", node_kinds={"n": "video"})
    return ctx


def test_exception_path_cancels_waits_for_jobs_and_never_retries_when_unsettled(tmp_path):
    backend = FakeBackend()
    backend.on("GET", r"/api/node-canvases/c1$", {"revision": 3})
    backend.on("POST", r"/batch-runs$", httpx.ConnectError("boom"))
    backend.on("GET", r"/batch-runs$", [_running_batch()])
    backend.on("POST", r"/batch-runs/b1/cancel", _running_batch())
    backend.on("GET", r"/api/jobs/j1", {"status": "running"})
    backend.on("GET", r"/runs$", {"spent": 0.5})
    guard = bench.BudgetGuard(limit=100, allow_unpriced=True)
    runner = bench.Runner(_api(backend), _cfg(tmp_path, settle_timeout=0.05), guard, log=lambda _m: None)
    ctx = _step_ctx()
    with pytest.raises(bench.BudgetStop, match="未结算"):
        runner.batch_step(ctx, "videos", ["video"], "video")
    posts = [p for m, p in backend.calls if m == "POST" and p.endswith("/batch-runs")]
    assert len(posts) == 1  # 在途任务没结算，绝不重新提交
    assert any(p.endswith("/batch-runs/b1/cancel") for _, p in backend.calls)  # 先取消
    assert guard.spent == pytest.approx(0.5)  # 异常路径也把已花费入账


def test_exception_path_retries_only_after_jobs_settled_and_spend_is_recorded(tmp_path):
    backend = FakeBackend()
    state = {"posted": 0}

    def post_batch(request):
        state["posted"] += 1
        if state["posted"] == 1:
            raise httpx.ReadTimeout("lost response")
        return httpx.Response(202, json={"id": "b2", "status": "running"})

    spent = iter([0.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    backend.on("GET", r"/api/node-canvases/c1$", {"revision": 3})
    backend.on("POST", r"/batch-runs$", post_batch)
    backend.on("GET", r"/batch-runs$", [{"id": "b1", "status": "canceled", "nodes": {"n": {"job_id": "j1"}}}])
    backend.on("GET", r"/api/jobs/j1", {"status": "canceled"})
    backend.on(
        "GET",
        r"/batch-runs/b2$",
        {"id": "b2", "status": "succeeded", "nodes": {"n": {"status": "succeeded"}}},
    )
    backend.on("GET", r"/runs$", lambda _r: httpx.Response(200, json={"spent": next(spent)}))
    guard = bench.BudgetGuard(limit=100, allow_unpriced=True)
    runner = bench.Runner(_api(backend), _cfg(tmp_path), guard, log=lambda _m: None)
    ctx = _step_ctx()
    outcome = runner.batch_step(ctx, "videos", ["video"], "video")
    assert outcome["attempts"] == 2 and state["posted"] == 2
    assert guard.spent == pytest.approx(1.0)  # 第一次异常期间产生的 1.0 已入账，且没有重复计算


def test_mock_media_is_invalid_but_mock_tts_is_allowed_with_zero_cost(tmp_path):
    jobs = {
        "j-img": {
            "status": "succeeded",
            "provider": "mock_image",
            "result": {"mocked": True},
            "max_attempts": 3,
        },
        "j-tts": {
            "status": "succeeded",
            "provider": "mock_tts",
            "result": {"mocked": True},
            "max_attempts": 3,
        },
    }
    backend = FakeBackend()
    backend.on(
        "GET", r"/api/jobs/(.+)$", lambda r: httpx.Response(200, json=jobs[r.url.path.rsplit("/", 1)[1]])
    )
    runner = bench.Runner(_api(backend), _cfg(tmp_path), bench.BudgetGuard(limit=None), log=lambda _m: None)
    ctx = bench.CaseCtx(canvas_id="c1", node_kinds={"n-img": "image", "n-tts": "audio"})
    found = runner.inspect_jobs(
        ctx,
        {
            "nodes": {
                "n-img": {"job_id": "j-img", "status": "succeeded"},
                "n-tts": {"job_id": "j-tts", "status": "succeeded"},
            }
        },
    )
    assert found["mock_media"] == ["image"] and found["tts_mocked"] is True and found["attempts"] == 3
    only_tts = runner.inspect_jobs(ctx, {"nodes": {"n-tts": {"job_id": "j-tts", "status": "succeeded"}}})
    assert only_tts["mock_media"] == [] and only_tts["tts_mocked"] is True


def test_mock_split_without_allow_flag_stops_with_invalid_mock(tmp_path):
    backend = FakeBackend()
    split = {
        "characters": [],
        "shots": [{"title": "a", "prompt": "p", "characters": [], "duration_s": 4, "lines": []}],
        "mocked": True,
    }
    backend.on("POST", r"/copilot/split$", split)
    backend.on("GET", r"/runs$", {"spent": 0})
    runner = bench.Runner(_api(backend), _cfg(tmp_path), bench.BudgetGuard(limit=10), log=lambda _m: None)
    case = runner.run_case(_story())
    assert case["status"] == "invalid_mock" and case["ok"] is False and case["failed_step"] == "split"
    assert case["canvas_id"] == "c1" and runner.stop_reason  # 记录完整保留，且基准被停止


def test_split_is_estimated_before_the_call_and_charged_after(tmp_path):
    backend = FakeBackend()
    backend.on(
        "POST",
        r"/copilot/split$",
        {"characters": [], "shots": [{"title": "a", "prompt": "p"}], "mocked": False},
    )
    backend.on("GET", r"/runs$", {"spent": 0})
    guard = bench.BudgetGuard(limit=0.0001)  # 估算 × 3 超过上限 → 调用前就拦住
    runner = bench.Runner(_api(backend), _cfg(tmp_path), guard, log=lambda _m: None)
    case = runner.run_case(_story())
    assert case["status"] == "budget_stopped" and case["failed_step"] == "split"
    assert not any(p.endswith("/copilot/split") for m, p in backend.calls if m == "POST")
    # 预算足够时：画布用量里没有拆分花费 → 按单次估算保守入账
    guard = bench.BudgetGuard(limit=10, allow_unpriced=True)
    runner = bench.Runner(_api(backend), _cfg(tmp_path), guard, log=lambda _m: None)
    ctx = bench.CaseCtx(canvas_id="c1")
    runner.charge_split(ctx, 0.01)
    assert guard.spent == pytest.approx(0.01) and ctx.split_extra == pytest.approx(0.01)


def test_preflight_refuses_when_server_cap_exceeds_budget_plus_spent(tmp_path):
    backend = FakeBackend()
    backend.on("GET", r"/runs$", {"spent": 0, "budget": {"cap": 20.0, "spent": 2.0, "in_flight": 0.0}})
    runner = bench.Runner(_api(backend), _cfg(tmp_path), bench.BudgetGuard(limit=5))
    with pytest.raises(bench.BudgetStop, match="DAILY_BUDGET_CNY"):
        runner.preflight_budget(5)
    ok = FakeBackend()
    ok.on("GET", r"/runs$", {"spent": 0, "budget": {"cap": 7.0, "spent": 2.0, "in_flight": 0.0}})
    bench.Runner(_api(ok), _cfg(tmp_path), bench.BudgetGuard(limit=5)).preflight_budget(5)
    assert ("DELETE", "/api/node-canvases/c1") in ok.calls  # 预检用的临时画布被清掉


# ── Mock 对话模型（ALLOW_MOCK_PROVIDERS 下拆分镜头兜底）──────────────────


def test_mock_split_uses_structured_shot_lines():
    from app.llm.mock_chat import build_mock_split

    prompt = "画幅 9:16，大约 2 个镜头。想法：\n" + bench.build_idea(_story(shots=6), 3)
    result = build_mock_split(prompt)
    assert [c["name"] for c in result["characters"]] == ["甲", "乙"]
    assert len(result["shots"]) == 2
    assert result["shots"][1]["characters"] == ["甲", "乙"]
    assert result["shots"][0]["lines"] == [{"speaker": "旁白", "text": "情节1"}]
    assert "画面1" in result["shots"][0]["prompt"]


def test_mock_split_free_text_and_valid_for_split_result():
    from app.llm.mock_chat import build_mock_split
    from app.services.node_canvas_service import SplitResult

    result = build_mock_split("画幅 9:16，大约 3 个镜头。想法：\n雨夜。他走进店里。猫在哭。")
    assert [s["prompt"] for s in result["shots"]] == ["雨夜。", "他走进店里。", "猫在哭。"]
    SplitResult.model_validate(result)
    assert len(build_mock_split("画幅 9:16，大约 5 个镜头。想法：\n只有一句")["shots"]) == 5


def test_call_llm_mock_fallback_only_without_any_real_chat_model(monkeypatch):
    from app.llm import factory
    from app.services import node_canvas_service as service

    def no_model(*args, **kwargs):
        raise RuntimeError("没有对话模型")

    monkeypatch.setattr(factory, "create_llm", no_model)
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "画幅 9:16，大约 2 个镜头。想法：\n雨夜。猫哭。"},
    ]
    # 没有任何真实对话模型 + 开了 mock：假结果，并带 mocked 标记
    monkeypatch.setattr(service, "_has_real_chat_model", lambda: False)
    monkeypatch.setenv("ALLOW_MOCK_PROVIDERS", "true")
    raw = json.loads(asyncio.run(service._call_llm(messages)))
    assert len(raw["shots"]) == 2 and raw["mocked"] is True
    assert service.SplitResult.model_validate(raw).mocked is True
    # 没开 mock：照常报错
    monkeypatch.setenv("ALLOW_MOCK_PROVIDERS", "false")
    with pytest.raises(service.CanvasError, match="还没有可用的对话模型"):
        asyncio.run(service._call_llm(messages))
    # 配了真实模型但创建失败（配置错误）：即使开了 mock 也必须报错，不能换成假结果
    monkeypatch.setattr(service, "_has_real_chat_model", lambda: True)
    monkeypatch.setenv("ALLOW_MOCK_PROVIDERS", "true")
    with pytest.raises(service.CanvasError, match="对话模型不可用"):
        asyncio.run(service._call_llm(messages))


# ── EvalRun：完整基准不变，局部运行单独标记 ───────────────────────────────


def test_eval_run_full_benchmark_still_requires_every_shot_sample():
    from app.services import eval_service

    dataset = SimpleNamespace(status="active", shot_count=3, key="k", version=1, content_hash="h")
    for samples in ([], [{}], [{}] * 4):
        with pytest.raises(ValueError, match="需要 3 个镜头样本"):
            asyncio.run(
                eval_service.create_eval_run(
                    None, dataset=dataset, provider="p", deployment="d", model="m", samples=samples
                )
            )


def test_eval_run_partial_must_declare_shots_and_match_sample_count():
    from app.services import eval_service

    dataset = SimpleNamespace(status="active", shot_count=3, key="k", version=1, content_hash="h")
    for partial, samples in ((3, [{}] * 3), (0, []), (2, [{}]), (1, [{}, {}])):
        with pytest.raises(ValueError, match="局部运行"):
            asyncio.run(
                eval_service.create_eval_run(
                    None,
                    dataset=dataset,
                    provider="p",
                    deployment="d",
                    model="m",
                    samples=samples,
                    partial_shots=partial,
                )
            )


class _FakeSession:
    """只够 create_eval_run / routing_stats 用的假会话。"""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.added = []

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        return None

    async def refresh(self, row):
        return None

    async def execute(self, _stmt):
        rows = self.rows

        class _Result:
            def scalars(self):
                return iter(rows)

        return _Result()


def test_partial_eval_run_gets_its_own_hash_and_is_excluded_from_routing_stats():
    from app.services import eval_service

    dataset = SimpleNamespace(
        id="d1", status="active", shot_count=5, key="k", version=1, content_hash="abc123"
    )
    session = _FakeSession()
    samples = [{"success": True, "latency_ms": 10, "cost_cny": 1}] * 2
    partial = asyncio.run(
        eval_service.create_eval_run(
            session,
            dataset=dataset,
            provider="p",
            deployment="d",
            model="m",
            samples=samples,
            partial_shots=2,
        )
    )
    assert partial.input_hash == "abc123:partial-2" and partial.metrics["partial"] == {"shots": 2, "of": 5}
    full = asyncio.run(
        eval_service.create_eval_run(
            session, dataset=dataset, provider="p", deployment="d", model="m", samples=samples * 2 + [{}]
        )
    )
    assert full.input_hash == "abc123" and "partial" not in full.metrics
    for row in (partial, full):
        row.status = "completed"
    stats = asyncio.run(eval_service.routing_stats(_FakeSession([partial, full])))
    assert len(stats) == 1 and stats[0]["sample_runs"] == 1  # 只统计完整基准那一条


def _split_ok():
    return {
        "characters": [],
        "shots": [{"title": "a", "prompt": "p", "characters": [], "duration_s": 4, "lines": []}],
        "mocked": False,
    }


def test_split_is_called_once_never_retried_and_charged_on_api_error(tmp_path):
    backend = FakeBackend()
    backend.on("POST", r"/copilot/split$", lambda _r: httpx.Response(422, json={"detail": "格式无法识别"}))
    backend.on("GET", r"/runs$", {"spent": 0})
    guard = bench.BudgetGuard(limit=10)
    runner = bench.Runner(_api(backend), _cfg(tmp_path), guard, log=lambda _m: None)
    case = runner.run_case(_story())
    assert case["failed_step"] == "split" and case["status"] == "failed"
    assert len([1 for m, p in backend.calls if m == "POST" and p.endswith("/copilot/split")]) == 1
    assert guard.spent == pytest.approx(case["split_cost_estimated"]) and guard.spent > 0


def test_split_charged_on_timeout_and_other_exceptions(tmp_path):
    backend = FakeBackend()
    backend.on("POST", r"/copilot/split$", httpx.ReadTimeout("厂商已收费但响应超时"))
    backend.on("GET", r"/runs$", {"spent": 0})
    guard = bench.BudgetGuard(limit=10)
    runner = bench.Runner(_api(backend), _cfg(tmp_path), guard, log=lambda _m: None)
    case = runner.run_case(_story())
    assert case["ok"] is False and case["failed_step"] == "split"
    assert guard.spent == pytest.approx(case["split_cost_estimated"])  # 异常也按估算入账
    assert guard.spent > 0
    assert case["cost_yuan"] == pytest.approx(guard.spent)


def test_max_split_calls_limits_total_split_requests(tmp_path):
    backend = FakeBackend()
    backend.on("POST", r"/copilot/split$", _split_ok())
    backend.on("GET", r"/runs$", {"spent": 0})
    runner = bench.Runner(
        _api(backend), _cfg(tmp_path, max_split_calls=1), bench.BudgetGuard(limit=10), log=lambda _m: None
    )
    ctx = bench.CaseCtx(canvas_id="c1")
    runner.split(_story(), ctx, {})
    with pytest.raises(bench.BudgetStop, match="max-split-calls"):
        runner.split(_story(), ctx, {})
    assert len([1 for m, p in backend.calls if m == "POST" and p.endswith("/copilot/split")]) == 1


def _runs_with_jobs(node_jobs):
    return {
        "spent": 0,
        "nodes": {node: {"jobs": [{"id": job} for job in jobs]} for node, jobs in node_jobs.items()},
    }


def test_mock_outputs_from_first_batch_are_caught_even_when_retry_would_skip_them(tmp_path):
    """首批已产出 mock 图片 → 轮询失败 → 结算后核对旧批次任务 → invalid_mock，不会进入会跳过它们的重试。"""
    backend = FakeBackend()
    backend.on("GET", r"/api/node-canvases/c1$", {"revision": 3})
    backend.on("POST", r"/batch-runs$", httpx.ReadTimeout("轮询前连接断开"))
    backend.on(
        "GET", r"/batch-runs$", [{"id": "b1", "status": "canceled", "nodes": {"n": {"job_id": "j-old"}}}]
    )
    backend.on(
        "GET",
        r"/api/jobs/j-old",
        {"status": "succeeded", "provider": "mock_image", "result": {"mocked": True}},
    )
    backend.on("GET", r"/runs$", _runs_with_jobs({"n": ["j-old"]}))
    runner = bench.Runner(_api(backend), _cfg(tmp_path), bench.BudgetGuard(limit=100), log=lambda _m: None)
    ctx = bench.CaseCtx(canvas_id="c1", node_kinds={"n": "image"})
    with pytest.raises(bench.MockDetected):
        runner.batch_step(ctx, "keyframes", ["image"], "image")
    assert len([1 for m, p in backend.calls if m == "POST" and p.endswith("/batch-runs")]) == 1  # 没有重试


def test_reused_skipped_outputs_are_traced_back_to_their_jobs_before_render(tmp_path):
    # 节点在批次里是 skipped（job_id 为空），但它复用的产物来自 mock 任务
    backend = FakeBackend()
    jobs = {
        "j-real": {"status": "succeeded", "provider": "volcano_image", "result": {"mocked": False}},
        "j-mock": {"status": "succeeded", "provider": "mock_image", "result": {"mocked": True}},
    }
    backend.on(
        "GET", r"/api/jobs/(.+)$", lambda r: httpx.Response(200, json=jobs[r.url.path.rsplit("/", 1)[1]])
    )
    backend.on("GET", r"/runs$", _runs_with_jobs({"k1": ["j-real"], "k2": ["j-mock"]}))
    runner = bench.Runner(_api(backend), _cfg(tmp_path), bench.BudgetGuard(limit=100), log=lambda _m: None)
    ctx = bench.CaseCtx(canvas_id="c1", node_kinds={"k1": "image", "k2": "image"})
    with pytest.raises(bench.MockDetected, match="mock"):
        runner.verify_media(ctx, "render")
    # 全是真实产物则通过；--allow-mock-media 只标记不抛
    ok = bench.Runner(_api(backend), _cfg(tmp_path), bench.BudgetGuard(limit=100), log=lambda _m: None)
    backend.handlers = [h for h in backend.handlers if h[1] != r"/runs$"]
    backend.on("GET", r"/runs$", _runs_with_jobs({"k1": ["j-real"]}))
    ok.verify_media(bench.CaseCtx(canvas_id="c1", node_kinds={"k1": "image"}), "render")
    lenient = bench.Runner(
        _api(backend),
        _cfg(tmp_path, allow_mock_media=True),
        bench.BudgetGuard(limit=100),
        log=lambda _m: None,
    )
    backend.handlers = [h for h in backend.handlers if h[1] != r"/runs$"]
    backend.on("GET", r"/runs$", _runs_with_jobs({"k2": ["j-mock"]}))
    ctx2 = bench.CaseCtx(canvas_id="c1", node_kinds={"k2": "image"})
    lenient.verify_media(ctx2, "render")
    assert ctx2.mock_media is True


def test_allow_mock_media_requires_budget_and_no_real_deployments(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCH_USERNAME", "u")
    monkeypatch.setenv("BENCH_PASSWORD", "p")
    assert bench.main(["--allow-mock-media", "--stories", "1"]) == 2  # 不给预算：拒绝（不登录、不请求）
    backend = FakeBackend()
    backend.on(
        "GET",
        r"/api/model-deployments$",
        [
            {"slug": "volcano-video", "modality": "video", "enabled": False},
            {"slug": "seedream", "modality": "image", "enabled": True},
            {"slug": "qwen-tts", "modality": "speech", "enabled": True},
        ],
    )
    runner = bench.Runner(_api(backend), _cfg(tmp_path), bench.BudgetGuard(limit=5))
    with pytest.raises(bench.BudgetStop, match="seedream"):
        runner.ensure_no_real_deployments()
    clean = FakeBackend()
    clean.on(
        "GET", r"/api/model-deployments$", [{"slug": "volcano-video", "modality": "video", "enabled": False}]
    )
    bench.Runner(_api(clean), _cfg(tmp_path), bench.BudgetGuard(limit=5)).ensure_no_real_deployments()


def test_check_prices_refuses_on_inconsistent_server_report(tmp_path):
    backend = FakeBackend()
    backend.on(
        "GET", r"/api/eval/price-consistency$", {"ok": False, "problems": ["图片部署 x：落账静态价目缺失"]}
    )
    runner = bench.Runner(_api(backend), _cfg(tmp_path), bench.BudgetGuard(limit=5))
    with pytest.raises(bench.BudgetStop, match="价格不一致.*落账静态价目缺失"):
        runner.check_prices("x", "", "preview")
    good = FakeBackend()
    good.on("GET", r"/api/eval/price-consistency$", {"ok": True, "problems": []})
    bench.Runner(_api(good), _cfg(tmp_path), bench.BudgetGuard(limit=5)).check_prices("", "", "preview")


# ── 服务端价格一致性函数 / 接口 ───────────────────────────────────────────


def _catalog(monkeypatch, deployments, routes=None):
    from app.model_runtime import catalog as catalog_module

    routes = routes or {}
    snapshot = SimpleNamespace(
        get=lambda slug: deployments.get(slug),
        route_candidates=lambda purpose, tier="final": (
            routes.get((purpose, tier)) or routes.get((purpose, "final")) or ()
        ),
    )
    monkeypatch.setattr(catalog_module, "get_catalog", lambda: SimpleNamespace(snapshot=snapshot))


def _dep(modality, model, pricing, name="volcano_image", driver="volcano_image"):
    return SimpleNamespace(
        modality=modality, model=model, pricing=pricing, driver=driver, instance=SimpleNamespace(name=name)
    )


def test_price_consistency_flags_missing_and_underquoted_image_prices(monkeypatch):
    from decimal import Decimal

    from app.providers import pricing as pricing_module
    from app.services import eval_service

    monkeypatch.setattr(
        pricing_module,
        "estimate_image_cost",
        lambda provider, model, count=1: {"seedream": Decimal("0.30"), "cheap": Decimal("0.10")}.get(model),
    )
    video = _dep(
        "video",
        "seedance",
        {"currency": "CNY", "output_per_million_tokens": "4.20"},
        name="volcano_video",
        driver="volcano_video",
    )
    deployments = {
        "good": _dep("image", "seedream", {"output_per_image": "0.30"}),
        "under": _dep("image", "seedream", {"output_per_image": "0.20"}),  # 落账价 0.30 > 0.20 × 1.05
        "noprice": _dep("image", "unknown-model", {"output_per_image": "0.1"}),  # 无静态价目
        "noquote": _dep("image", "cheap", {}),  # Catalog 无报价
        "vid": video,
    }
    _catalog(
        monkeypatch,
        deployments,
        {("image_generation", "final"): ("good",), ("shot_video", "preview"): ("vid",)},
    )
    ok = eval_service.price_consistency()
    assert ok["ok"] is True and ok["image"][0]["quote"] == "0.30" and ok["video"][0]["ok"] is True
    for slug, needle in (
        ("under", "高于报价"),
        ("noprice", "落账静态价目缺失"),
        ("noquote", "Catalog 报价缺失"),
    ):
        result = eval_service.price_consistency(image_model=slug, video_model="vid")
        assert result["ok"] is False and needle in "；".join(result["problems"])
    missing = eval_service.price_consistency(image_model="nope", video_model="vid")
    assert missing["ok"] is False and "不存在" in missing["problems"][0]


def test_price_consistency_endpoint_is_admin_only_read_only():
    from app.auth.dependencies import require_admin
    from app.routers import eval as eval_router

    route = next(r for r in eval_router.router.routes if r.path == "/eval/price-consistency")
    assert route.methods == {"GET"}
    assert require_admin in [dep.call for dep in route.dependant.dependencies]
