#!/usr/bin/env python3
"""Inventory academic-video source media with ffprobe."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from video_common import (
    ToolError,
    atomic_write_json,
    discover_media,
    find_rotation,
    media_duration,
    normalize_container,
    probe_media,
    rational_to_float,
    resolve_tool,
    sha256_file,
)


def stream_inventory(stream: dict[str, Any]) -> dict[str, Any]:
    codec_type = stream.get("codec_type")
    result: dict[str, Any] = {
        "index": stream.get("index"),
        "type": codec_type,
        "codec": stream.get("codec_name"),
        "codec_long_name": stream.get("codec_long_name"),
        "bit_rate": int(stream["bit_rate"]) if str(stream.get("bit_rate", "")).isdigit() else None,
        "duration": float(stream["duration"]) if _is_float(stream.get("duration")) else None,
        "tags": stream.get("tags") or {},
    }
    if codec_type == "video":
        average_fps = rational_to_float(stream.get("avg_frame_rate"))
        nominal_fps = rational_to_float(stream.get("r_frame_rate"))
        result.update(
            {
                "width": stream.get("width"),
                "height": stream.get("height"),
                "coded_width": stream.get("coded_width"),
                "coded_height": stream.get("coded_height"),
                "pixel_format": stream.get("pix_fmt"),
                "sample_aspect_ratio": stream.get("sample_aspect_ratio"),
                "display_aspect_ratio": stream.get("display_aspect_ratio"),
                "average_fps": average_fps,
                "nominal_fps": nominal_fps,
                "average_frame_rate_raw": stream.get("avg_frame_rate"),
                "nominal_frame_rate_raw": stream.get("r_frame_rate"),
                "vfr_risk": bool(
                    average_fps
                    and nominal_fps
                    and abs(average_fps - nominal_fps) > max(0.01, nominal_fps * 0.001)
                ),
                "rotation_degrees": find_rotation(stream),
                "color_space": stream.get("color_space"),
                "color_transfer": stream.get("color_transfer"),
                "color_primaries": stream.get("color_primaries"),
                "frame_count": int(stream["nb_frames"]) if str(stream.get("nb_frames", "")).isdigit() else None,
            }
        )
    elif codec_type == "audio":
        result.update(
            {
                "sample_rate": int(stream["sample_rate"]) if str(stream.get("sample_rate", "")).isdigit() else None,
                "channels": stream.get("channels"),
                "channel_layout": stream.get("channel_layout"),
                "sample_format": stream.get("sample_fmt"),
            }
        )
    return result


def _is_float(value: Any) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def inspect(path: Path, ffprobe: str, include_hash: bool) -> dict[str, Any]:
    probe = probe_media(path, ffprobe)
    streams = [stream_inventory(stream) for stream in probe.get("streams", []) if isinstance(stream, dict)]
    videos = [stream for stream in streams if stream.get("type") == "video"]
    audios = [stream for stream in streams if stream.get("type") == "audio"]
    format_info = probe.get("format") or {}
    item: dict[str, Any] = {
        "path": str(path),
        "filename": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path) if include_hash else None,
        "duration_seconds": media_duration(probe),
        "format_name": format_info.get("format_name"),
        "container": normalize_container(format_info.get("format_name"), path.suffix),
        "bit_rate": int(format_info["bit_rate"]) if str(format_info.get("bit_rate", "")).isdigit() else None,
        "stream_count": len(streams),
        "has_video": bool(videos),
        "has_audio": bool(audios),
        "primary_video": videos[0] if videos else None,
        "primary_audio": audios[0] if audios else None,
        "streams": streams,
        "probe_error": None,
    }
    return item


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan video, audio, and image inputs with ffprobe and write media_inventory.json."
    )
    parser.add_argument("inputs", nargs="+", type=Path, help="Files or directories to inspect.")
    parser.add_argument("--output", type=Path, default=Path("media_inventory.json"))
    parser.add_argument("--recursive", action="store_true", help="Recurse into input directories.")
    parser.add_argument("--ffprobe", help="Explicit ffprobe executable path.")
    parser.add_argument("--hash", choices=("sha256", "none"), default="sha256")
    parser.add_argument("--strict", action="store_true", help="Return 1 if an input is missing or cannot be probed.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        ffprobe = resolve_tool(args.ffprobe, "ffprobe")
    except ToolError as exc:
        print(f"NOT AUDITABLE: {exc}", file=sys.stderr)
        return 2

    paths = discover_media(args.inputs, args.recursive)
    explicit_missing = [str(path) for path in args.inputs if not path.expanduser().exists()]
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = [
        {"path": value, "error": "input does not exist"} for value in explicit_missing
    ]
    for path in paths:
        try:
            items.append(inspect(path, ffprobe, args.hash == "sha256"))
        except (OSError, ToolError) as exc:
            errors.append({"path": str(path), "error": str(exc)})

    inventory = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ffprobe": ffprobe,
        "input_count": len(paths),
        "items": items,
        "errors": errors,
        "status": "PASS" if not errors else ("FAIL" if args.strict else "WARN"),
    }
    atomic_write_json(args.output.resolve(), inventory)
    print(f"{inventory['status']}: inspected {len(items)} media file(s); {len(errors)} error(s).")
    print(args.output.resolve())
    return 1 if errors and args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
