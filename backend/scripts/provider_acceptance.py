#!/usr/bin/env python3
"""供应商预检与 MiniMax H3 真实付费基准工具。"""

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env", override=False)
load_dotenv(BACKEND_DIR.parent / ".env", override=False)

from app.providers.base import VideoGenerationRequest
from app.providers.minimax_video import MiniMaxVideoProvider, h3_experimental_enabled
from app.providers.pricing import PRICE_TABLE_VERSION, estimate_video_cost, snapshot_price
from app.providers.tts_base import QwenTTSProvider
from app.providers.volcano_video import VolcanoVideoProvider

PAID_CONFIRMATION = "I_UNDERSTAND_THIS_WILL_CHARGE"
_DURATION_PATTERN = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def parse_durations(value: str) -> list[int]:
    durations = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not durations or any(duration < 4 or duration > 15 for duration in durations):
        raise argparse.ArgumentTypeError("H3 durations 必须是 4-15 秒的逗号分隔整数")
    return durations


def probe_duration(path: Path) -> float | None:
    try:
        import imageio_ffmpeg

        result = subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        match = _DURATION_PATTERN.search(result.stderr or "")
        if not match:
            return None
        hours, minutes, seconds = match.groups()
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except Exception:
        return None


def build_preflight(durations: list[int], resolution: str) -> dict:
    credential_state = {
        "MINIMAX_API_KEY": bool(os.getenv("MINIMAX_API_KEY", "").strip()),
        "VOLCANO_API_KEY": bool(os.getenv("VOLCANO_API_KEY", "").strip()),
        "DASHSCOPE_API_KEY": bool(os.getenv("DASHSCOPE_API_KEY", "").strip()),
    }
    capability_provider = MiniMaxVideoProvider(
        api_key=os.getenv("MINIMAX_API_KEY", "").strip() or "preflight-only"
    )
    capabilities = []
    for duration in durations:
        request = VideoGenerationRequest(
            mode="text",
            prompt="FishStudio H3 capability preflight",
            duration=duration,
            resolution=resolution,
            quality="standard",
            provider_policy="explicit",
            explicit_provider="minimax",
        )
        capabilities.append(
            {
                "duration": duration,
                "supports_h3": capability_provider.supports(
                    request, model=capability_provider.h3_model
                ),
                "estimated_cost": str(
                    estimate_video_cost(
                        "minimax",
                        capability_provider.h3_model,
                        resolution,
                        output_seconds=duration,
                    )
                ),
            }
        )
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "paid_calls_executed": False,
        "credentials": credential_state,
        "providers": {
            "minimax": MiniMaxVideoProvider().is_available(),
            "volcano": VolcanoVideoProvider().is_available(),
            "dashscope_tts": QwenTTSProvider().is_available(),
        },
        "minimax_h3": {
            "experimental_enabled": h3_experimental_enabled(),
            "model": capability_provider.h3_model,
            "resolution": resolution,
            "capabilities": capabilities,
        },
        "minimax_low_cost": {
            "model": "MiniMax-Hailuo-02",
            "mode": "first_frame",
            "resolution": "512P",
            "durations": [6, 10],
            "estimated_costs": {"6": "0.600000", "10": "1.000000"},
        },
        "price_table_version": PRICE_TABLE_VERSION,
    }


async def run_video_case(
    provider: MiniMaxVideoProvider,
    request: VideoGenerationRequest,
    model: str,
    expected_protocol: str,
    poll_interval: float,
    timeout: float,
) -> dict:
    estimated_cost = estimate_video_cost(
        "minimax", model, request.resolution, output_seconds=request.duration
    )
    record = {
        "mode": request.mode,
        "duration": request.duration,
        "resolution": request.resolution,
        "model": model,
        "protocol": expected_protocol,
        "estimated_cost": str(estimated_cost) if estimated_cost is not None else None,
        "price_snapshot": snapshot_price("minimax", model, request.resolution),
        "status": "submitting",
    }
    started = time.monotonic()
    submit = await provider.submit(request, model=model)
    record["submit_latency_seconds"] = round(time.monotonic() - started, 3)
    record["external_request_id"] = submit.external_request_id
    record["submit_metadata"] = submit.raw_metadata
    if not submit.success:
        record.update(
            status="submit_failed",
            error_code=submit.error_code,
            error_message=submit.error_message,
        )
        return record

    protocol = str(submit.raw_metadata.get("protocol") or expected_protocol)
    deadline = time.monotonic() + timeout
    polls = 0
    status_result = None
    while time.monotonic() < deadline:
        polls += 1
        status_result = await provider.get_status(submit.external_request_id, protocol=protocol)
        if status_result.success or status_result.error_code != "pending":
            break
        await asyncio.sleep(poll_interval)
    record["poll_count"] = polls
    if status_result is None or status_result.error_code == "pending":
        record.update(status="timeout", error_code="timeout", error_message="轮询超时")
        return record
    if not status_result.success:
        record.update(
            status="task_failed",
            error_code=status_result.error_code,
            error_message=status_result.error_message,
            status_metadata=status_result.raw_metadata,
        )
        return record

    fetched = await provider.fetch_result(submit.external_request_id, protocol=protocol)
    record["total_latency_seconds"] = round(time.monotonic() - started, 3)
    record["status_metadata"] = status_result.raw_metadata
    record["fetch_metadata"] = fetched.raw_metadata
    if not fetched.success or not fetched.artifacts:
        record.update(
            status="fetch_failed",
            error_code=fetched.error_code,
            error_message=fetched.error_message,
        )
        return record

    storage_key = str(fetched.artifacts[0].get("storage_key") or "")
    local_path = BACKEND_DIR / "storage" / storage_key
    record.update(
        status="succeeded",
        storage_key=storage_key,
        actual_duration_seconds=probe_duration(local_path),
        supplier_usage=status_result.raw_metadata.get("usage", {}),
    )
    return record


async def run_h3_case(
    provider: MiniMaxVideoProvider,
    duration: int,
    resolution: str,
    prompt: str,
    poll_interval: float,
    timeout: float,
) -> dict:
    request = VideoGenerationRequest(
        mode="text",
        prompt=prompt,
        duration=duration,
        resolution=resolution,
        quality="standard",
        provider_policy="explicit",
        explicit_provider="minimax",
    )
    return await run_video_case(
        provider,
        request,
        provider.h3_model,
        "h3",
        poll_interval,
        timeout,
    )


async def execute_benchmark(args) -> dict:
    if args.confirm_paid_calls != PAID_CONFIRMATION:
        raise RuntimeError(
            "真实基准会产生费用；必须显式传入 "
            f"--confirm-paid-calls {PAID_CONFIRMATION}"
        )
    if not os.getenv("MINIMAX_API_KEY", "").strip():
        raise RuntimeError("MINIMAX_API_KEY 未配置")
    if not h3_experimental_enabled():
        raise RuntimeError("MINIMAX_H3_EXPERIMENTAL 未开启，拒绝执行 H3 付费请求")

    provider = MiniMaxVideoProvider()
    cases = []
    for duration in args.durations:
        cases.append(
            await run_h3_case(
                provider,
                duration,
                args.resolution,
                args.prompt,
                args.poll_interval,
                args.timeout,
            )
        )
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "paid_calls_executed": True,
        "provider": "minimax",
        "model": provider.h3_model,
        "protocol": "h3",
        "price_table_version": PRICE_TABLE_VERSION,
        "cases": cases,
        "summary": {
            "total": len(cases),
            "succeeded": sum(case["status"] == "succeeded" for case in cases),
            "estimated_cost": str(
                sum(
                    (
                        estimate_video_cost(
                            "minimax",
                            provider.h3_model,
                            args.resolution,
                            output_seconds=duration,
                        )
                        or 0
                    )
                    for duration in args.durations
                )
            ),
        },
    }


async def execute_hailuo_02_benchmark(args) -> dict:
    if args.confirm_paid_calls != PAID_CONFIRMATION:
        raise RuntimeError(
            "真实基准会产生费用；必须显式传入 "
            f"--confirm-paid-calls {PAID_CONFIRMATION}"
        )
    if not os.getenv("MINIMAX_API_KEY", "").strip():
        raise RuntimeError("MINIMAX_API_KEY 未配置")
    if args.first_frame is None:
        raise RuntimeError("Hailuo-02 512P 低成本测试必须传入 --first-frame")

    first_frame = args.first_frame.expanduser().resolve()
    if not first_frame.is_file():
        raise RuntimeError(f"首帧图片不存在: {first_frame}")

    provider = MiniMaxVideoProvider()
    model = "MiniMax-Hailuo-02"
    request = VideoGenerationRequest(
        mode="first_frame",
        prompt=args.prompt,
        first_frame=str(first_frame),
        duration=args.hailuo_duration,
        resolution="512P",
        quality="preview",
        provider_policy="explicit",
        explicit_provider="minimax",
    )
    if not provider.supports(request, model=model):
        raise RuntimeError("当前 MiniMax Adapter 不支持该 Hailuo-02 请求")

    case = await run_video_case(
        provider,
        request,
        model,
        "v1",
        args.poll_interval,
        args.timeout,
    )
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "paid_calls_executed": True,
        "provider": "minimax",
        "model": model,
        "protocol": "v1",
        "first_frame": str(first_frame),
        "price_table_version": PRICE_TABLE_VERSION,
        "cases": [case],
        "summary": {
            "total": 1,
            "succeeded": int(case["status"] == "succeeded"),
            "estimated_cost": case["estimated_cost"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--durations", type=parse_durations, default=parse_durations("5,10,15"))
    parser.add_argument("--resolution", choices=["768P", "2K"], default="768P")
    parser.add_argument("--prompt", default="电影感短剧镜头，人物自然行走，运镜平稳，画面连贯")
    execution = parser.add_mutually_exclusive_group()
    execution.add_argument("--execute-minimax-h3", action="store_true")
    execution.add_argument("--execute-hailuo-02", action="store_true")
    parser.add_argument("--first-frame", type=Path)
    parser.add_argument("--hailuo-duration", type=int, choices=[6, 10], default=6)
    parser.add_argument("--confirm-paid-calls", default="")
    parser.add_argument("--poll-interval", type=float, default=10.0)
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        if args.execute_hailuo_02:
            report = asyncio.run(execute_hailuo_02_benchmark(args))
        elif args.execute_minimax_h3:
            report = asyncio.run(execute_benchmark(args))
        else:
            report = build_preflight(args.durations, args.resolution)
    except Exception as error:
        report = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "paid_calls_executed": False,
            "status": "blocked",
            "error": str(error),
        }
        exit_code = 2
    else:
        cases = report.get("cases") or []
        exit_code = 1 if cases and any(case.get("status") != "succeeded" for case in cases) else 0

    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
