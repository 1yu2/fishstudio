"""画布成片的出片检查（只用 ffmpeg，不依赖 ffprobe）。

每次画布渲染成功后由 Worker 对输出文件运行，结果写进成片 Artifact 的 metadata.qc 和 job.result.qc：
- 阻止（status=failed）：playable / has_audio / av_duration_diff_ms / duration_match / missing_shots / aigc_label；
- 提醒（status=warning）：black_segments / frozen_segments / silent_segments / subtitle_overflow；
- loudness_lufs 只记录；全部通过 status=passed；检查本身出错（超时等）status=error，按提醒处理、不阻止。

qc 的形状（前端直接用）：
{"version", "status", "checks": {名称: {"status": passed|failed|warning|skipped|info, ...}},
 "issues": [{"code", "level": blocking|warning, "message", "start_ms"?, "end_ms"?}], "notes": [str]}
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Optional, Sequence

from app.services.timeline_renderer import _ffmpeg_exe, merge_ranges

QC_VERSION = 1
AV_DIFF_LIMIT_MS = 200
DURATION_LIMIT_MS = 200
BLACK_MIN_S = 0.5
BLACK_PIX_TH = 0.10  # 与剧集侧 publish_service._probe_file 一致
FREEZE_MIN_S = 2.0
SILENCE_MIN_S = 2.0
SILENCE_DB = -50
SUBTITLE_MAX_LINES = 2
SUBTITLE_MAX_CHARS = 40
PROBE_TIMEOUT_S = 300

Range = tuple[int, int]


class QcExecError(RuntimeError):
    """检查进程本身执行失败（无法启动、被杀、资源不足、滤镜初始化失败等）：整次检查记为 error，不判成片好坏。"""


# ─────────────────────────────────────────
# 区间运算（ms）
# ─────────────────────────────────────────


def _subtract(ranges: Sequence[Range], excluded: Sequence[Range]) -> list[Range]:
    out: list[Range] = []
    for start, end in ranges:
        pieces = [(start, end)]
        for ex_start, ex_end in merge_ranges(excluded):
            nxt: list[Range] = []
            for a, b in pieces:
                if ex_end <= a or ex_start >= b:
                    nxt.append((a, b))
                    continue
                if ex_start > a:
                    nxt.append((a, ex_start))
                if ex_end < b:
                    nxt.append((ex_end, b))
            pieces = nxt
        out.extend(pieces)
    return out


def _intersect(ranges: Sequence[Range], included: Sequence[Range]) -> list[Range]:
    out: list[Range] = []
    for start, end in ranges:
        for in_start, in_end in merge_ranges(included):
            a, b = max(start, in_start), min(end, in_end)
            if b > a:
                out.append((a, b))
    return out


def _at_least(ranges: Sequence[Range], min_ms: float) -> list[Range]:
    return [(s, e) for s, e in ranges if e - s >= min_ms]


def audio_coverage(spec: dict[str, Any], total_ms: int) -> list[Range]:
    """时间线上有音频的时间段：配音按起止，BGM（循环到全片）按起点到片尾。"""
    ranges: list[Range] = []
    for track in spec.get("audio_tracks") or []:
        for clip in track.get("clips") or []:
            start = int(clip.get("timeline_start_ms") or 0)
            source_out = clip.get("source_out_ms")
            if clip.get("kind") == "bgm" or not source_out:
                ranges.append((start, total_ms))
            else:
                ranges.append((start, start + max(0, int(source_out) - int(clip.get("source_in_ms") or 0))))
    return [(s, min(e, total_ms)) for s, e in merge_ranges(ranges) if s < total_ms]


# ─────────────────────────────────────────
# ffmpeg 读取
# ─────────────────────────────────────────


def _ffmpeg(args: list[str], timeout: int = PROBE_TIMEOUT_S) -> tuple[str, int]:
    """跑一次 ffmpeg，返回 (stderr 文本, 返回码)。无法启动抛 QcExecError。"""
    return _ffmpeg_raw(args, timeout)[1:]


def _ffmpeg_raw(args: list[str], timeout: int = PROBE_TIMEOUT_S) -> tuple[str, str, int]:
    """(stdout, stderr, 返回码)。"""
    try:
        proc = subprocess.run(
            [_ffmpeg_exe(), "-hide_banner", "-nostdin", *args], capture_output=True, timeout=timeout
        )
    except OSError as e:
        raise QcExecError(f"无法启动 ffmpeg：{e}") from e
    return (
        proc.stdout.decode("utf-8", errors="replace"),
        proc.stderr.decode("utf-8", errors="replace"),
        proc.returncode,
    )


def _clock_ms(match: "re.Match[str]") -> int:
    hours, minutes, seconds = match.groups()
    return round((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000)


_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_TIME = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")
_FPS = re.compile(r"([\d.]+) fps")


def read_container(path: Path) -> dict[str, Any]:
    """容器信息：时长、有无音视频轨、帧率、format 级元数据标签。"""
    text, _ = _ffmpeg(["-i", str(path)], timeout=60)
    duration = _DURATION.search(text)
    tags: dict[str, str] = {}
    head = text.split("Duration:", 1)[0]
    in_meta = False
    for line in head.splitlines():
        if line.strip() == "Metadata:":
            in_meta = True
            continue
        if in_meta:
            match = re.match(r"^\s{4}(\S+)\s*:\s?(.*)$", line)
            if match:
                tags[match.group(1)] = match.group(2)
    fps = None
    for line in text.splitlines():
        if "Video:" in line and "attached pic" not in line:
            found = _FPS.search(line)
            fps = float(found.group(1)) if found else None
    return {
        "duration_ms": _clock_ms(duration) if duration else None,
        "has_video": any("Video:" in line and "attached pic" not in line for line in text.splitlines()),
        "has_audio": "Audio:" in text,
        "fps": fps,
        "tags": tags,
    }


def _stream_end_ms(path: Path, kind: str) -> int:
    """单条轨道的结束时间：framecrc 逐包输出 pts / duration（单位见 #tb），取 max(pts + duration)。

    读不出来抛 QcExecError（不能当作“没问题”）。
    """
    out, err, code = _ffmpeg_raw(
        ["-i", str(path), "-map", f"0:{kind}:0", "-c", "copy", "-f", "framecrc", "-"], 120
    )
    if code != 0:
        raise QcExecError(f"读取{'视频' if kind == 'v' else '音频'}轨时间戳失败（退出码 {code}）")
    tb = re.search(r"^#tb 0:\s*(\d+)/(\d+)", out, re.M)
    if not tb or int(tb.group(2)) == 0:
        raise QcExecError("读取轨道时间基失败")
    num, den = int(tb.group(1)), int(tb.group(2))
    end = None
    for line in out.splitlines():
        if line.startswith("#"):
            continue
        fields = [f.strip() for f in line.split(",")]
        if len(fields) < 4:
            continue
        try:
            value = int(fields[2]) + int(fields[3])
        except ValueError:
            continue
        end = value if end is None else max(end, value)
    if end is None:
        raise QcExecError("轨道里没有读到任何数据包")
    return round(end * num / den * 1000)


# 解码失败里“确实是文件坏了”的特征；其余非零退出（被杀、资源不足）按执行故障处理
_DECODE_ERRORS = (
    "invalid data",
    "moov atom not found",
    "error while decoding",
    "corrupt",
    "invalid nal",
    "truncated",
    "partial file",
    "end of file",
    "no such file",
    "could not find codec",
    "output file is empty",
    "does not contain any stream",
)
_RESOURCE_ERRORS = (
    "cannot allocate memory",
    "out of memory",
    "resource temporarily unavailable",
    "too many open files",
)


def _decodes_fully(path: Path) -> tuple[bool, str]:
    text, code = _ffmpeg(["-v", "error", "-i", str(path), "-f", "null", "-"])
    low = text.lower()
    if code == 0 and not text.strip():
        return True, ""
    if code < 0 or code >= 128 or any(item in low for item in _RESOURCE_ERRORS):
        raise QcExecError(f"解码进程异常退出（退出码 {code}）")
    if code != 0 and not text.strip():
        raise QcExecError(f"解码进程异常退出（退出码 {code}）")
    if any(item in low for item in _DECODE_ERRORS) or code == 0:
        return False, text.strip()[-300:]
    raise QcExecError(f"解码进程失败（退出码 {code}）：{text.strip()[-120:]}")


def _events(text: str, start_re: str, end_re: str, total_ms: int) -> list[Range]:
    """按出现顺序配对 start / end；最后一个没有 end 的区间持续到片尾。"""
    found = [(m.start(), "s", float(m.group(1))) for m in re.finditer(start_re, text)]
    found += [(m.start(), "e", float(m.group(1))) for m in re.finditer(end_re, text)]
    ranges: list[Range] = []
    open_at: Optional[float] = None
    for _, kind, value in sorted(found):
        if kind == "s":
            open_at = value
        elif open_at is not None:
            ranges.append((round(open_at * 1000), round(value * 1000)))
            open_at = None
    if open_at is not None:
        ranges.append((round(open_at * 1000), total_ms))
    return ranges


def _evidence(path: Path, total_ms: int, has_audio: bool = True) -> dict[str, Any]:
    """一遍解码里同时跑黑屏 / 冻帧 / 静音 / 响度检测。"""
    text, code = _ffmpeg(
        [
            "-v",
            "info",
            "-i",
            str(path),
            "-vf",
            f"blackdetect=d={BLACK_MIN_S}:pix_th={BLACK_PIX_TH},freezedetect=n=-50dB:d={FREEZE_MIN_S}",
            *(
                ["-af", f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN_S},ebur128=peak=true"]
                if has_audio
                else []
            ),
            "-f",
            "null",
            "-",
        ]
    )
    lufs = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", text)
    # 返回码非零，或 ebur128 汇总没出现（滤镜没初始化 / 进程中途被终止）：空事件不能当作“没发现问题”
    if code != 0 or (has_audio and "Summary:" not in text):
        raise QcExecError(f"内容检测没有正常完成（退出码 {code}）")
    return {
        "black": _events(text, r"black_start:\s*(-?[\d.]+)", r"black_end:\s*(-?[\d.]+)", total_ms),
        "freeze": _events(text, r"freeze_start:\s*(-?[\d.]+)", r"freeze_end:\s*(-?[\d.]+)", total_ms),
        "silence": _events(text, r"silence_start:\s*(-?[\d.]+)", r"silence_end:\s*(-?[\d.]+)", total_ms),
        "loudness": float(lufs[-1]) if lufs else None,
    }


# ─────────────────────────────────────────
# 文案
# ─────────────────────────────────────────


def _seconds(ms: int) -> str:
    return f"{ms / 1000:.1f}"


def _issue(
    code: str, level: str, message: str, start_ms: Optional[int] = None, end_ms: Optional[int] = None
) -> dict:
    item: dict[str, Any] = {"code": code, "level": level, "message": message}
    if start_ms is not None:
        item["start_ms"] = int(start_ms)
        item["end_ms"] = int(end_ms if end_ms is not None else start_ms)
    return item


def check_subtitles(cues: Sequence[dict[str, Any]], total_ms: int) -> list[dict]:
    """字幕超出时间线范围，或单条超过 2 行 / 40 字。只看 EDL。"""
    items: list[dict] = []
    for cue in cues:
        text = str(cue.get("text") or "")
        start, end = int(cue.get("start_ms") or 0), int(cue.get("end_ms") or 0)
        label = text.replace("\n", " ")[:10]
        if start < 0 or end > total_ms:
            items.append(_issue("subtitle_overflow", "warning", f"字幕「{label}」超出了视频时长", start, end))
            continue
        lines = [line for line in text.splitlines() if line.strip()] or [text]
        chars = len(text.replace("\n", ""))
        if len(lines) > SUBTITLE_MAX_LINES or chars > SUBTITLE_MAX_CHARS:
            items.append(
                _issue(
                    "subtitle_overflow",
                    "warning",
                    f"字幕「{label}…」太长（{len(lines)} 行 / {chars} 字），可能显示不全",
                    start,
                    end,
                )
            )
    return items


# ─────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────


def check_render(
    path: Path,
    *,
    total_ms: int,
    shots: Sequence[dict[str, Any]],
    audio_ranges: Sequence[Range],
    expects_audio: bool,
    cues: Sequence[dict[str, Any]] = (),
    badge_applied: bool = False,
) -> dict[str, Any]:
    """对成片文件跑全部检查并分级。可能抛 subprocess.TimeoutExpired 等，由调用方转成 status=error。

    shots：manifest.shots（start = offset_ms，长度 = duration_ms，placeholder / still 标记）；
    audio_ranges：时间线上有音频的时间段；expects_audio：EDL 里有配音或 BGM；
    badge_applied：渲染时是否叠加了角标（来自渲染结果）。
    """
    checks: dict[str, dict[str, Any]] = {}
    issues: list[dict] = []
    notes: list[str] = []

    # 缺素材：渲染前已有检查，这里记录结果
    missing = [i + 1 for i, shot in enumerate(shots) if shot.get("placeholder")]
    checks["missing_shots"] = {"status": "failed" if missing else "passed", "shots": missing}
    for number in missing:
        issues.append(_issue("missing_shots", "blocking", f"镜头 {number} 的视频素材缺失"))

    ok, decode_error = _decodes_fully(path) if path.is_file() else (False, "文件不存在")
    checks["playable"] = {"status": "passed" if ok else "failed", "error": decode_error}
    if not ok:
        issues.append(_issue("playable", "blocking", "视频无法完整解码，请重新渲染"))

    info = (
        read_container(path)
        if path.is_file()
        else {"duration_ms": None, "has_audio": False, "has_video": False, "tags": {}, "fps": None}
    )
    actual_ms = info["duration_ms"]
    if ok and actual_ms is None:
        raise QcExecError("读取成片时长失败")

    # 声音轨
    has_audio_ok = info["has_audio"] or not expects_audio
    checks["has_audio"] = {
        "status": "passed" if has_audio_ok else "failed",
        "has_audio_track": info["has_audio"],
        "expected": expects_audio,
    }
    if not has_audio_ok:
        issues.append(_issue("has_audio", "blocking", "时间线里有配音或背景音乐，但成片没有声音"))

    # 音画时长差
    diff: Optional[int] = None
    if info["has_audio"] and info["has_video"] and ok:
        # 按包的 pts + duration 取各轨结束时间；读不出来抛 QcExecError（整次检查记为 error）
        diff = _stream_end_ms(path, "a") - _stream_end_ms(path, "v")
    diff_ok = diff is None or abs(diff) <= AV_DIFF_LIMIT_MS
    checks["av_duration_diff_ms"] = {"status": "passed" if diff_ok else "failed", "value": diff}
    if not diff_ok and diff is not None:
        issues.append(
            _issue(
                "av_duration_diff_ms",
                "blocking",
                f"声音比画面{'长' if diff > 0 else '短'} {_seconds(abs(diff))} 秒",
            )
        )

    # 成片时长与 EDL 一致
    gap = abs(actual_ms - total_ms) if actual_ms is not None else None
    match_ok = gap is not None and gap <= DURATION_LIMIT_MS
    checks["duration_match"] = {
        "status": "passed" if match_ok else "failed",
        "expected_ms": total_ms,
        "actual_ms": actual_ms,
        "diff_ms": None if actual_ms is None else actual_ms - total_ms,
    }
    if not match_ok and ok:
        issues.append(
            _issue(
                "duration_match",
                "blocking",
                f"成片时长 {_seconds(actual_ms or 0)} 秒，与时间线的 {_seconds(total_ms)} 秒不一致",
            )
        )

    # AIGC 标识：读回元数据 + 确认渲染用了角标
    meta_ok = _aigc_metadata_ok(info["tags"])
    label_ok = meta_ok and badge_applied
    checks["aigc_label"] = {
        "status": "passed" if label_ok else "failed",
        "metadata": meta_ok,
        "badge": badge_applied,
    }
    if not label_ok:
        issues.append(_issue("aigc_label", "blocking", "成片缺少“AI 生成”标识，请重新渲染"))

    # 画面 / 声音内容检查（读不出来就跳过，不重复报错）
    if ok:
        evidence = _evidence(path, actual_ms or total_ms, info["has_audio"])
        still = [
            (int(shot["offset_ms"]), int(shot["offset_ms"]) + int(shot["duration_ms"]))
            for shot in shots
            if shot.get("still")
        ]
        black = _at_least(evidence["black"], BLACK_MIN_S * 1000)
        frozen = _at_least(_subtract(evidence["freeze"], still), FREEZE_MIN_S * 1000)
        if audio_ranges:
            silent = _at_least(_intersect(evidence["silence"], audio_ranges), SILENCE_MIN_S * 1000)
        else:
            silent = []
            notes.append("时间线上没有配音和背景音乐，成片是静音的")
        for code, ranges, name in (
            ("black_segments", black, "黑屏"),
            ("frozen_segments", frozen, "画面静止"),
            ("silent_segments", silent, "静音"),
        ):
            checks[code] = {
                "status": "warning" if ranges else "passed",
                "segments": [{"start_ms": s, "end_ms": e} for s, e in ranges],
            }
            if code == "silent_segments" and not audio_ranges:
                checks[code]["no_audio"] = True
            for start, end in ranges:
                issues.append(_issue(code, "warning", f"{name} {_seconds(end - start)} 秒", start, end))
        checks["loudness_lufs"] = {"status": "info", "value": evidence["loudness"]}
    else:
        for code in ("black_segments", "frozen_segments", "silent_segments", "loudness_lufs"):
            checks[code] = {"status": "skipped"}

    overflow = check_subtitles(cues, total_ms)
    checks["subtitle_overflow"] = {
        "status": "warning" if overflow else "passed",
        "count": len(overflow),
    }
    issues.extend(overflow)

    blocking = [item for item in issues if item["level"] == "blocking"]
    status = "failed" if blocking else "warning" if issues else "passed"
    return {"version": QC_VERSION, "status": status, "checks": checks, "issues": issues, "notes": notes}


def _aigc_metadata_ok(tags: dict[str, str]) -> bool:
    """comment 与 aigc 两个标签都要能解析出 AIGC.Label == "1"。"""
    for key in ("comment", "aigc"):
        try:
            if json.loads(tags.get(key) or "")["AIGC"]["Label"] != "1":
                return False
        except (ValueError, KeyError, TypeError):
            return False
    return True


def error_report(reason: str) -> dict[str, Any]:
    """检查本身出错（ffmpeg 超时等）：按提醒处理，不阻止导出。"""
    return {
        "version": QC_VERSION,
        "status": "error",
        "error": reason[:300],
        "checks": {},
        "issues": [],
        "notes": [],
    }
