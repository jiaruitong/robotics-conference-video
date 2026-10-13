#!/usr/bin/env python3
"""Shared deterministic helpers for academic-video scripts."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Sequence


MEDIA_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts", ".m2ts",
    ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg",
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp",
}

ACADEMIC_MODES = frozenset({"supplementary", "presentation", "teaser"})


class ToolError(RuntimeError):
    """A dependency or child-process failure with a user-facing message."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ToolError(f"Input file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ToolError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ToolError(f"JSON root must be an object: {path}")
    return value


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_tool(explicit: str | None, name: str) -> str:
    candidate = explicit or shutil.which(name)
    if not candidate:
        raise ToolError(
            f"Required executable '{name}' was not found. Install it or pass its path explicitly."
        )
    path = Path(candidate)
    if path.exists():
        return str(path.resolve())
    found = shutil.which(candidate)
    if found:
        return found
    raise ToolError(f"Executable is not usable: {candidate}")


def run_process(
    argv: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = [str(item) for item in argv]
    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        if len(detail) > 4000:
            detail = detail[-4000:]
        raise ToolError(
            f"Command failed with exit code {result.returncode}: {' '.join(command)}\n{detail}"
        )
    return result


def parse_timecode(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("empty timecode")
        if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
            result = float(text)
        else:
            match = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)", text)
            if not match:
                raise ValueError(f"invalid timecode: {value!r}")
            hours = int(match.group(1) or 0)
            minutes = int(match.group(2))
            seconds = float(match.group(3))
            if minutes >= 60 or seconds >= 60:
                raise ValueError(f"invalid timecode: {value!r}")
            result = hours * 3600 + minutes * 60 + seconds
    else:
        raise ValueError(f"invalid timecode value: {value!r}")
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"timecode must be finite and non-negative: {value!r}")
    return result


def format_timecode(seconds: float, *, decimal: str = ".") -> str:
    milliseconds = max(0, round(float(seconds) * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}{decimal}{milliseconds:03d}"


def rational_to_float(value: Any) -> float | None:
    if value in (None, "", "0/0", "N/A"):
        return None
    try:
        return float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError):
        return None


def probe_media(path: Path, ffprobe: str) -> dict[str, Any]:
    result = run_process(
        [
            ffprobe,
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            "-show_programs",
            str(path),
        ]
    )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ToolError(f"ffprobe returned invalid JSON for {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ToolError(f"ffprobe returned an invalid object for {path}")
    return value


def first_stream(probe: dict[str, Any], codec_type: str) -> dict[str, Any] | None:
    for stream in probe.get("streams", []):
        if isinstance(stream, dict) and stream.get("codec_type") == codec_type:
            return stream
    return None


def media_duration(probe: dict[str, Any]) -> float | None:
    candidates: list[Any] = [probe.get("format", {}).get("duration")]
    candidates.extend(stream.get("duration") for stream in probe.get("streams", []))
    parsed: list[float] = []
    for value in candidates:
        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(duration) and duration >= 0:
            parsed.append(duration)
    return max(parsed) if parsed else None


def normalize_container(format_name: str | None, suffix: str | None = None) -> str | None:
    names = {item.strip().lower() for item in (format_name or "").split(",") if item.strip()}
    ext = (suffix or "").lower().lstrip(".")
    if "mp4" in names or ("mov" in names and ext == "mp4"):
        return "mp4"
    if "matroska" in names and ext == "webm":
        return "webm"
    if "matroska" in names:
        return "mkv"
    return ext or (sorted(names)[0] if names else None)


def find_rotation(stream: dict[str, Any]) -> float:
    tags = stream.get("tags") or {}
    if "rotate" in tags:
        try:
            return float(tags["rotate"])
        except (TypeError, ValueError):
            pass
    for item in stream.get("side_data_list") or []:
        if isinstance(item, dict) and "rotation" in item:
            try:
                return float(item["rotation"])
            except (TypeError, ValueError):
                pass
    return 0.0


def discover_media(inputs: Iterable[Path], recursive: bool) -> list[Path]:
    discovered: set[Path] = set()
    for value in inputs:
        path = value.expanduser().resolve()
        if path.is_file():
            discovered.add(path)
            continue
        if path.is_dir():
            iterator = path.rglob("*") if recursive else path.glob("*")
            discovered.update(
                item.resolve()
                for item in iterator
                if item.is_file() and item.suffix.lower() in MEDIA_EXTENSIONS
            )
    return sorted(discovered, key=lambda item: str(item).casefold())


def resolve_asset_path(storyboard_path: Path, asset: dict[str, Any]) -> Path:
    raw = asset.get("path")
    if not isinstance(raw, str) or not raw.strip():
        raise ToolError(f"Asset {asset.get('asset_id', '<unknown>')} has no path")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = storyboard_path.parent / path
    return path.resolve()


def asset_index(storyboard: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for asset in storyboard.get("assets", []):
        if not isinstance(asset, dict):
            continue
        asset_id = asset.get("asset_id")
        if isinstance(asset_id, str) and asset_id:
            if asset_id in result:
                raise ToolError(f"Duplicate asset_id: {asset_id}")
            result[asset_id] = asset
    return result


def segment_times(segment: dict[str, Any]) -> tuple[float, float, float]:
    start = parse_timecode(segment.get("start_time"))
    end = parse_timecode(segment.get("end_time"))
    declared = segment.get("duration")
    duration = parse_timecode(declared) if declared is not None else end - start
    return start, end, duration


def timeline_findings(storyboard: dict[str, Any], tolerance: float = 0.002) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    segments = storyboard.get("segments")
    if not isinstance(segments, list) or not segments:
        return [{
            "id": "NO_SEGMENTS",
            "severity": "P0",
            "message": "Storyboard has no segments.",
        }]
    previous_end = 0.0
    seen: set[str] = set()
    for position, segment in enumerate(segments):
        if not isinstance(segment, dict):
            findings.append({"id": "INVALID_SEGMENT", "severity": "P0", "message": f"Segment {position} is not an object."})
            continue
        segment_id = str(segment.get("segment_id") or f"index-{position}")
        if segment_id in seen:
            findings.append({"id": "DUPLICATE_SEGMENT_ID", "severity": "P0", "message": f"Duplicate segment_id: {segment_id}"})
        seen.add(segment_id)
        try:
            start, end, duration = segment_times(segment)
        except (ValueError, TypeError) as exc:
            findings.append({"id": "INVALID_SEGMENT_TIME", "severity": "P0", "message": f"{segment_id}: {exc}"})
            continue
        if end <= start:
            findings.append({"id": "INVALID_SEGMENT_TIME", "severity": "P0", "message": f"{segment_id}: end_time must be after start_time."})
        if abs((end - start) - duration) > tolerance:
            findings.append({"id": "DURATION_MISMATCH", "severity": "P0", "message": f"{segment_id}: duration differs from end-start."})
        if start > previous_end + tolerance:
            findings.append({"id": "TIMELINE_GAP", "severity": "P0", "message": f"Gap before {segment_id}: {start - previous_end:.3f} s."})
        elif start < previous_end - tolerance:
            findings.append({"id": "TIMELINE_OVERLAP", "severity": "P0", "message": f"Overlap before {segment_id}: {previous_end - start:.3f} s."})
        previous_end = max(previous_end, end)
    return findings


def disclosure_strings(segment: dict[str, Any]) -> list[str]:
    strings: list[str] = []
    for overlay in segment.get("overlay_text", []) or []:
        if isinstance(overlay, str):
            strings.append(overlay)
        elif isinstance(overlay, dict) and overlay.get("text") is not None:
            strings.append(str(overlay["text"]))
    return strings


def json_safe_command(argv: Sequence[str | os.PathLike[str]]) -> list[str]:
    return [str(item) for item in argv]


def academic_mode_findings(storyboard: dict[str, Any]) -> list[dict[str, str]]:
    """Require an explicitly selected academic video mode."""
    project = storyboard.get("project") if isinstance(storyboard.get("project"), dict) else {}
    mode = project.get("mode")
    if not isinstance(mode, str) or mode not in ACADEMIC_MODES:
        return [{
            "id": "ACADEMIC_MODE_INVALID",
            "severity": "P0",
            "message": "Select supplementary, presentation, or teaser in project.mode.",
        }]
    return []
