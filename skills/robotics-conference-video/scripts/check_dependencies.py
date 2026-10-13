#!/usr/bin/env python3
"""Audit the local open-source video toolchain without mutating the environment."""

from __future__ import annotations

import argparse
import importlib.util
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from video_common import ToolError, atomic_write_json, resolve_tool, run_process


REQUIRED_FILTERS = {
    "scale", "pad", "crop", "overlay", "hstack", "vstack", "concat", "atempo",
    "drawtext", "blackdetect", "silencedetect", "astats", "freezedetect", "fps",
}
RECOMMENDED_FILTERS = {"subtitles", "vidstabdetect", "vidstabtransform"}
REQUIRED_ENCODERS = {"libx264", "aac"}


def first_line(text: str) -> str:
    return text.splitlines()[0].strip() if text.splitlines() else "unknown"


def parse_capability_names(text: str, kind: str) -> set[str]:
    names: set[str] = set()
    if kind == "filter":
        pattern = re.compile(r"^\s*[TSC\.]{2,4}\s+([A-Za-z0-9_]+)\s", re.MULTILINE)
    else:
        pattern = re.compile(r"^\s*[VASDFX\.]{6}\s+([A-Za-z0-9_]+)\s", re.MULTILINE)
    names.update(pattern.findall(text))
    return names


def find_fonts() -> list[str]:
    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ]
    return [str(path.resolve()) for path in candidates if path.is_file()]


def check(args: argparse.Namespace) -> dict[str, Any]:
    required_missing: list[str] = []
    warnings: list[str] = []
    tools: dict[str, Any] = {}

    if sys.version_info < (3, 10):
        required_missing.append("Python >= 3.10")

    try:
        ffmpeg = resolve_tool(args.ffmpeg, "ffmpeg")
        version = run_process([ffmpeg, "-version"]).stdout
        tools["ffmpeg"] = {"path": ffmpeg, "version": first_line(version)}
    except ToolError as exc:
        ffmpeg = None
        required_missing.append(str(exc))

    try:
        ffprobe = resolve_tool(args.ffprobe, "ffprobe")
        version = run_process([ffprobe, "-version"]).stdout
        tools["ffprobe"] = {"path": ffprobe, "version": first_line(version)}
    except ToolError as exc:
        ffprobe = None
        required_missing.append(str(exc))

    filters: set[str] = set()
    encoders: set[str] = set()
    if ffmpeg:
        try:
            filters_text = run_process([ffmpeg, "-hide_banner", "-filters"]).stdout
            encoders_text = run_process([ffmpeg, "-hide_banner", "-encoders"]).stdout
            filters = parse_capability_names(filters_text, "filter")
            encoders = parse_capability_names(encoders_text, "encoder")
        except ToolError as exc:
            required_missing.append(str(exc))
    for name in sorted(REQUIRED_FILTERS - filters):
        required_missing.append(f"FFmpeg filter: {name}")
    for name in sorted(REQUIRED_ENCODERS - encoders):
        required_missing.append(f"FFmpeg encoder: {name}")
    for name in sorted(RECOMMENDED_FILTERS - filters):
        warnings.append(f"Optional FFmpeg filter unavailable: {name}")
    if args.require_libass and "subtitles" not in filters:
        required_missing.append("FFmpeg subtitles/libass filter")

    python_packages = {
        "Pillow": {
            "installed": importlib.util.find_spec("PIL") is not None,
            "required": False,
            "purpose": "optional transparent annotation and contact-sheet generation",
        }
    }
    if not python_packages["Pillow"]["installed"]:
        warnings.append("Optional Python package Pillow is not installed.")

    fonts = find_fonts()
    if not fonts:
        warnings.append("No known sans-serif system font was found; pass --font-file when rendering text.")

    status = "FAIL" if required_missing else ("WARN" if warnings else "PASS")
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "platform": platform.platform(),
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
            "meets_minimum": sys.version_info >= (3, 10),
        },
        "tools": tools,
        "ffmpeg_capabilities": {
            "required_filters": {name: name in filters for name in sorted(REQUIRED_FILTERS)},
            "recommended_filters": {name: name in filters for name in sorted(RECOMMENDED_FILTERS)},
            "required_encoders": {name: name in encoders for name in sorted(REQUIRED_ENCODERS)},
        },
        "python_packages": python_packages,
        "fonts": fonts,
        "required_missing": required_missing,
        "warnings": warnings,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check Python, FFmpeg, ffprobe, codecs, filters, and fonts.")
    parser.add_argument("--ffmpeg", help="Explicit ffmpeg path.")
    parser.add_argument("--ffprobe", help="Explicit ffprobe path.")
    parser.add_argument("--json-out", type=Path, default=Path("dependencies.json"))
    parser.add_argument("--require-libass", action="store_true")
    parser.add_argument("--strict", action="store_true", help="Return 1 for warnings as well as missing requirements.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = check(args)
        atomic_write_json(args.json_out.resolve(), report)
    except Exception as exc:
        print(f"NOT AUDITABLE: {exc}", file=sys.stderr)
        return 2
    print(f"{report['status']}: dependency report written to {args.json_out.resolve()}")
    for item in report["required_missing"]:
        print(f"MISSING: {item}")
    for item in report["warnings"]:
        print(f"WARN: {item}")
    if report["required_missing"]:
        return 1
    if args.strict and report["warnings"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
