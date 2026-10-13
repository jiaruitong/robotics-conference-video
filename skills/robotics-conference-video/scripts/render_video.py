#!/usr/bin/env python3
"""Render a reproducible academic video from storyboard.json using FFmpeg."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from video_common import (
    ToolError,
    academic_mode_findings,
    asset_index,
    atomic_write_json,
    disclosure_strings,
    first_stream,
    json_safe_command,
    load_json,
    media_duration,
    parse_timecode,
    probe_media,
    resolve_asset_path,
    resolve_tool,
    run_process,
    segment_times,
    sha256_file,
    timeline_findings,
)


def number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def atempo_chain(speed: float) -> str:
    if speed <= 0:
        raise ToolError("speed_factor must be greater than zero")
    values: list[float] = []
    remaining = speed
    while remaining > 2.0 + 1e-9:
        values.append(2.0)
        remaining /= 2.0
    while remaining < 0.5 - 1e-9:
        values.append(0.5)
        remaining /= 0.5
    values.append(remaining)
    return ",".join(f"atempo={value:.8g}" for value in values)


def escape_filter_text(value: str) -> str:
    return (
        value.replace("\\", r"\\")
        .replace("'", r"\'")
        .replace(":", r"\:")
        .replace("%", r"\%")
        .replace("\n", r"\n")
    )


def escape_filter_path(path: Path) -> str:
    value = path.resolve().as_posix().replace("'", r"\'")
    if len(value) >= 2 and value[1] == ":":
        value = value[0] + r"\:" + value[2:]
    return value


def coordinate(value: Any, total: int, default: float) -> int:
    parsed = number(value, default)
    return round(parsed * total) if -1.0 <= parsed <= 1.0 else round(parsed)


def source_references(segment: dict[str, Any]) -> list[dict[str, Any]]:
    refs = segment.get("source_asset", [])
    if isinstance(refs, dict):
        refs = [refs]
    if refs is None:
        return []
    if not isinstance(refs, list) or not all(isinstance(item, dict) for item in refs):
        raise ToolError(f"{segment.get('segment_id')}: source_asset must be an object or list")
    return refs


def normalized_video_chain(
    input_label: str,
    output_label: str,
    *,
    width: int,
    height: int,
    fps: float,
    duration: float,
    speed: float,
    crop: dict[str, Any] | None,
) -> str:
    filters = [f"[{input_label}]setpts=PTS-STARTPTS"]
    if crop:
        crop_w = coordinate(crop.get("w"), width, 1.0)
        crop_h = coordinate(crop.get("h"), height, 1.0)
        crop_x = coordinate(crop.get("x"), width, 0.0)
        crop_y = coordinate(crop.get("y"), height, 0.0)
        filters.append(f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}")
    if abs(speed - 1.0) > 1e-9:
        filters.append(f"setpts=PTS/{speed:.8g}")
    filters.extend(
        [
            f"fps={fps:.8g}",
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos",
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x101418",
            "setsar=1",
            f"tpad=stop_mode=clone:stop_duration={duration:.6f}",
            f"trim=duration={duration:.6f}",
            "setpts=PTS-STARTPTS",
            f"format=yuv420p[{output_label}]",
        ]
    )
    return ",".join(filters)


def overlay_text_filter(
    input_label: str,
    output_label: str,
    overlay: dict[str, Any],
    *,
    width: int,
    height: int,
    duration: float,
    font_file: Path | None,
    default_font_size: int,
) -> str:
    text = escape_filter_text(str(overlay.get("text", "")))
    box = overlay.get("box") if isinstance(overlay.get("box"), dict) else {}
    x = coordinate(box.get("x", overlay.get("x")), width, 0.04)
    y = coordinate(box.get("y", overlay.get("y")), height, 0.04)
    font_size = max(12, round(number(overlay.get("font_size"), default_font_size)))
    start = max(0.0, number(overlay.get("start_offset"), 0.0))
    end = min(duration, number(overlay.get("end_offset"), duration))
    color = str(overlay.get("font_color", "white"))
    box_color = str(overlay.get("box_color", "black@0.62"))
    parts = [f"drawtext=text='{text}'"]
    if font_file:
        parts.append(f"fontfile='{escape_filter_path(font_file)}'")
    parts.extend(
        [
            f"fontcolor={color}",
            f"fontsize={font_size}",
            "box=1",
            f"boxcolor={box_color}",
            f"boxborderw={max(6, font_size // 3)}",
            f"x={x}",
            f"y={y}",
            f"enable='between(t,{start:.6f},{end:.6f})'",
        ]
    )
    return f"[{input_label}]{':'.join(parts)}[{output_label}]"


def find_default_font(language: str = "en") -> Path | None:
    if language.casefold().startswith("zh"):
        for candidate in (
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/simhei.ttf"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/System/Library/Fonts/PingFang.ttc"),
        ):
            if candidate.is_file():
                return candidate
    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    return next((path.resolve() for path in candidates if path.is_file()), None)


class Renderer:
    def __init__(
        self,
        storyboard_path: Path,
        storyboard: dict[str, Any],
        ffmpeg: str,
        ffprobe: str,
        font_file: Path | None,
        work_dir: Path,
    ) -> None:
        self.storyboard_path = storyboard_path
        self.storyboard = storyboard
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.font_file = font_file
        self.work_dir = work_dir
        self.assets = asset_index(storyboard)
        output = storyboard.get("output") if isinstance(storyboard.get("output"), dict) else {}
        resolution = output.get("resolution") if isinstance(output.get("resolution"), dict) else {}
        self.width = int(output.get("width", resolution.get("width", 1920)))
        self.height = int(output.get("height", resolution.get("height", 1080)))
        self.fps = number(output.get("fps"), 30.0)
        self.video_codec = str(output.get("encoder", "libx264"))
        self.audio_codec = str(output.get("audio_encoder", "aac"))
        self.preset = str(output.get("preset", "medium"))
        self.crf = int(output.get("crf", 18))
        self.audio_bitrate = str(output.get("audio_bitrate", "192k"))
        self.segment_records: list[dict[str, Any]] = []

    def _asset(self, asset_id: str) -> tuple[dict[str, Any], Path]:
        asset = self.assets.get(asset_id)
        if asset is None:
            raise ToolError(f"Unknown asset_id: {asset_id}")
        path = resolve_asset_path(self.storyboard_path, asset)
        if not path.is_file():
            raise ToolError(f"Missing asset {asset_id}: {path}")
        return asset, path

    def render_segment(self, segment: dict[str, Any], index: int) -> Path:
        segment_id = str(segment.get("segment_id") or f"S{index + 1:03d}")
        _, _, duration = segment_times(segment)
        speed = number(segment.get("speed_factor"), 1.0)
        if speed <= 0:
            raise ToolError(f"{segment_id}: speed_factor must be positive")
        visual = segment.get("visual") if isinstance(segment.get("visual"), dict) else {}
        layout = str(visual.get("layout", "single")).lower()
        refs = source_references(segment)
        if layout in {"side_by_side", "split_horizontal"} and len(refs) < 2:
            raise ToolError(f"{segment_id}: {layout} requires two source assets")
        if layout in {"top_bottom", "split_vertical", "picture_in_picture", "pip"} and len(refs) < 2:
            raise ToolError(f"{segment_id}: {layout} requires two source assets")

        argv: list[str] = [self.ffmpeg, "-hide_banner", "-loglevel", "warning", "-y"]
        resolved_refs: list[tuple[dict[str, Any], dict[str, Any], Path, dict[str, Any]]] = []
        for ref in refs:
            asset_id = str(ref.get("asset_id", ""))
            asset, path = self._asset(asset_id)
            kind = str(asset.get("kind", "video")).lower()
            in_time = parse_timecode(ref.get("in_time", 0))
            source_needed = duration * speed
            out_time = ref.get("out_time")
            if out_time is not None:
                available = parse_timecode(out_time) - in_time
                if available <= 0:
                    raise ToolError(f"{segment_id}/{asset_id}: out_time must be after in_time")
                source_needed = min(source_needed, available)
            if kind in {"image", "figure", "generated_card"}:
                # Static pixels need no output-rate decoding; the downstream fps filter preserves the timeline.
                argv.extend(["-loop", "1", "-framerate", "1", "-t", f"{duration:.6f}", "-i", str(path)])
                probe: dict[str, Any] = {}
            else:
                probe = probe_media(path, self.ffprobe)
                total_duration = media_duration(probe)
                if total_duration is not None and in_time + source_needed > total_duration + max(0.05, 1.0 / self.fps):
                    freeze_last = bool(ref.get("freeze_last", False))
                    if not freeze_last:
                        raise ToolError(
                            f"{segment_id}/{asset_id}: requested source range ends at "
                            f"{in_time + source_needed:.3f}s but asset duration is {total_duration:.3f}s"
                        )
                    source_needed = max(0.001, total_duration - in_time)
                argv.extend(["-ss", f"{in_time:.6f}", "-t", f"{source_needed:.6f}", "-i", str(path)])
            resolved_refs.append((ref, asset, path, probe))

        if not refs:
            background = str(visual.get("background_color", "0x0B1F33")).replace("#", "0x")
            argv.extend(
                [
                    "-f", "lavfi",
                    "-i", f"color=c={background}:s={self.width}x{self.height}:r={self.fps:.8g}:d={duration:.6f}",
                ]
            )

        graphic_overlays = visual.get("overlays", []) if isinstance(visual.get("overlays"), list) else []
        graphic_inputs: list[tuple[int, dict[str, Any]]] = []
        next_input = len(refs) if refs else 1
        for graphic in graphic_overlays:
            if not isinstance(graphic, dict) or not graphic.get("asset_id"):
                continue
            _, graphic_path = self._asset(str(graphic["asset_id"]))
            argv.extend(["-loop", "1", "-framerate", "1", "-t", f"{duration:.6f}", "-i", str(graphic_path)])
            graphic_inputs.append((next_input, graphic))
            next_input += 1

        narration = segment.get("narration") if isinstance(segment.get("narration"), dict) else {}
        narration_asset_id = narration.get("audio_asset_id")
        narration_input: int | None = None
        if narration_asset_id:
            _, narration_path = self._asset(str(narration_asset_id))
            offset = parse_timecode(narration.get("in_time", 0))
            argv.extend(["-ss", f"{offset:.6f}", "-t", f"{duration:.6f}", "-i", str(narration_path)])
            narration_input = next_input
            next_input += 1

        source_audio_input: int | None = None
        audio_config = dict(self.storyboard.get("audio")) if isinstance(self.storyboard.get("audio"), dict) else {}
        if isinstance(segment.get("audio"), dict):
            audio_config.update(segment["audio"])
        mix_source_with_narration = bool(audio_config.get("mix_source_with_narration", False))
        if (
            audio_config.get("use_source_audio", True)
            and resolved_refs
            and (narration_input is None or mix_source_with_narration)
        ):
            first_probe = resolved_refs[0][3]
            if first_probe and first_stream(first_probe, "audio") is not None:
                source_audio_input = 0
        silence_input: int | None = None
        if narration_input is None and source_audio_input is None:
            argv.extend(["-f", "lavfi", "-t", f"{duration:.6f}", "-i", "anullsrc=r=48000:cl=stereo"])
            silence_input = next_input

        graph: list[str] = []
        if not refs:
            graph.append(
                normalized_video_chain(
                    "0:v", "vbase", width=self.width, height=self.height, fps=self.fps,
                    duration=duration, speed=1.0, crop=None,
                )
            )
        elif layout in {"side_by_side", "split_horizontal"}:
            half = self.width // 2
            for source_index in range(2):
                ref = refs[source_index]
                graph.append(
                    normalized_video_chain(
                        f"{source_index}:v", f"v{source_index}", width=half, height=self.height,
                        fps=self.fps, duration=duration, speed=speed,
                        crop=ref.get("crop") if isinstance(ref.get("crop"), dict) else None,
                    )
                )
            graph.append("[v0][v1]hstack=inputs=2[vbase]")
        elif layout in {"top_bottom", "split_vertical"}:
            half = self.height // 2
            for source_index in range(2):
                ref = refs[source_index]
                graph.append(
                    normalized_video_chain(
                        f"{source_index}:v", f"v{source_index}", width=self.width, height=half,
                        fps=self.fps, duration=duration, speed=speed,
                        crop=ref.get("crop") if isinstance(ref.get("crop"), dict) else None,
                    )
                )
            graph.append("[v0][v1]vstack=inputs=2[vbase]")
        elif layout in {"picture_in_picture", "pip"}:
            graph.append(
                normalized_video_chain(
                    "0:v", "vmain", width=self.width, height=self.height, fps=self.fps,
                    duration=duration, speed=speed,
                    crop=refs[0].get("crop") if isinstance(refs[0].get("crop"), dict) else None,
                )
            )
            pip_width = coordinate(visual.get("pip_width"), self.width, 0.32)
            pip_height = max(2, round(pip_width * self.height / self.width))
            graph.append(
                normalized_video_chain(
                    "1:v", "vpip", width=pip_width, height=pip_height, fps=self.fps,
                    duration=duration, speed=speed,
                    crop=refs[1].get("crop") if isinstance(refs[1].get("crop"), dict) else None,
                )
            )
            pip_x = coordinate(visual.get("pip_x"), self.width, 0.64)
            pip_y = coordinate(visual.get("pip_y"), self.height, 0.62)
            graph.append(f"[vmain][vpip]overlay={pip_x}:{pip_y}:shortest=1[vbase]")
        else:
            graph.append(
                normalized_video_chain(
                    "0:v", "vbase", width=self.width, height=self.height, fps=self.fps,
                    duration=duration, speed=speed,
                    crop=refs[0].get("crop") if isinstance(refs[0].get("crop"), dict) else None,
                )
            )

        current_video = "vbase"
        for overlay_number, (input_index, graphic) in enumerate(graphic_inputs):
            width = coordinate(graphic.get("w"), self.width, 0.35)
            height = coordinate(graphic.get("h"), self.height, 0.35)
            x = coordinate(graphic.get("x"), self.width, 0.60)
            y = coordinate(graphic.get("y"), self.height, 0.10)
            start = number(graphic.get("start_offset"), 0.0)
            end = number(graphic.get("end_offset"), duration)
            scaled = f"graphic{overlay_number}"
            result = f"vgraphic{overlay_number}"
            graph.append(f"[{input_index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease[{scaled}]")
            # Static-overlay EOF must not shorten the normalized base or remove its last-frame graphic.
            graph.append(
                f"[{current_video}][{scaled}]overlay={x}:{y}:eof_action=repeat:repeatlast=1:shortest=0:"
                f"enable='between(t,{start:.6f},{end:.6f})'[{result}]"
            )
            current_video = result

        for box_number, box in enumerate(visual.get("boxes", []) or []):
            if not isinstance(box, dict):
                continue
            x = coordinate(box.get("x"), self.width, 0.1)
            y = coordinate(box.get("y"), self.height, 0.1)
            width = coordinate(box.get("w"), self.width, 0.2)
            height = coordinate(box.get("h"), self.height, 0.2)
            thickness = max(1, int(number(box.get("thickness"), 4)))
            color = str(box.get("color", "yellow@0.9"))
            start = number(box.get("start_offset"), 0.0)
            end = number(box.get("end_offset"), duration)
            result = f"vbox{box_number}"
            graph.append(
                f"[{current_video}]drawbox=x={x}:y={y}:w={width}:h={height}:color={color}:t={thickness}:"
                f"enable='between(t,{start:.6f},{end:.6f})'[{result}]"
            )
            current_video = result

        overlays = segment.get("overlay_text", []) or []
        if not isinstance(overlays, list):
            raise ToolError(f"{segment_id}: overlay_text must be a list")
        if layout in {"side_by_side", "split_horizontal", "top_bottom", "split_vertical"}:
            for ref_index, ref in enumerate(refs[:2]):
                if ref.get("label"):
                    if layout in {"side_by_side", "split_horizontal"}:
                        x = 0.04 if ref_index == 0 else 0.54
                        y = 0.05
                    else:
                        x = 0.04
                        y = 0.05 if ref_index == 0 else 0.55
                    overlays = list(overlays) + [{"kind": "comparison_label", "text": str(ref["label"]), "x": x, "y": y}]
        style = self.storyboard.get("style") if isinstance(self.storyboard.get("style"), dict) else {}
        default_font_size = max(24, int(number(style.get("overlay_font_size"), self.height * 0.04)))
        for overlay_number, overlay in enumerate(overlays):
            if isinstance(overlay, str):
                overlay = {"text": overlay}
            if not isinstance(overlay, dict) or not str(overlay.get("text", "")).strip():
                continue
            result = f"vtext{overlay_number}"
            graph.append(
                overlay_text_filter(
                    current_video, result, overlay, width=self.width, height=self.height,
                    duration=duration, font_file=self.font_file, default_font_size=default_font_size,
                )
            )
            current_video = result

        transition = segment.get("transition") if isinstance(segment.get("transition"), dict) else {}
        fade_duration = max(0.0, min(number(transition.get("duration"), 0.0), duration / 2.0))
        video_filters = [f"[{current_video}]setpts=PTS-STARTPTS"]
        if transition.get("in") == "fade" and fade_duration:
            video_filters.append(f"fade=t=in:st=0:d={fade_duration:.6f}")
        if transition.get("out") == "fade" and fade_duration:
            video_filters.append(f"fade=t=out:st={duration - fade_duration:.6f}:d={fade_duration:.6f}")
        video_filters.append("format=yuv420p[vout]")
        graph.append(",".join(video_filters))

        def normalized_audio_filter(
            audio_source: str,
            audio_speed: float,
            volume: float,
            output_label: str,
        ) -> str:
            filters = [f"[{audio_source}]asetpts=PTS-STARTPTS"]
            if abs(audio_speed - 1.0) > 1e-9:
                filters.append(atempo_chain(audio_speed))
            if abs(volume - 1.0) > 1e-9:
                filters.append(f"volume={volume:.8g}")
            filters.extend(
                [
                    "aresample=48000",
                    "aformat=sample_fmts=fltp:channel_layouts=stereo",
                    "apad",
                    f"atrim=duration={duration:.6f}[{output_label}]",
                ]
            )
            return ",".join(filters)

        if narration_input is not None and source_audio_input is not None:
            graph.append(
                normalized_audio_filter(
                    f"{narration_input}:a",
                    1.0,
                    number(audio_config.get("narration_volume"), 1.0),
                    "anarration",
                )
            )
            graph.append(
                normalized_audio_filter(
                    f"{source_audio_input}:a",
                    speed,
                    number(audio_config.get("source_volume"), 0.25),
                    "asource",
                )
            )
            graph.append(
                "[anarration][asource]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[abase]"
            )
        elif narration_input is not None:
            graph.append(
                normalized_audio_filter(
                    f"{narration_input}:a",
                    1.0,
                    number(audio_config.get("narration_volume", audio_config.get("volume")), 1.0),
                    "abase",
                )
            )
        elif source_audio_input is not None:
            graph.append(
                normalized_audio_filter(
                    f"{source_audio_input}:a",
                    speed,
                    number(audio_config.get("source_volume", audio_config.get("volume")), 1.0),
                    "abase",
                )
            )
        else:
            graph.append(normalized_audio_filter(f"{silence_input}:a", 1.0, 1.0, "abase"))

        audio_filters = ["[abase]atrim=duration={:.6f}".format(duration)]
        if transition.get("in") == "fade" and fade_duration:
            audio_filters.append(f"afade=t=in:st=0:d={fade_duration:.6f}")
        if transition.get("out") == "fade" and fade_duration:
            audio_filters.append(f"afade=t=out:st={duration - fade_duration:.6f}:d={fade_duration:.6f}")
        audio_filters.append("alimiter=limit=0.95[aout]")
        graph.append(",".join(audio_filters))

        output_path = self.work_dir / f"segment_{index + 1:04d}.mp4"
        argv.extend(
            [
                "-filter_complex", ";".join(graph),
                "-map", "[vout]", "-map", "[aout]",
                "-c:v", self.video_codec,
                "-preset", self.preset,
                "-crf", str(self.crf),
                "-pix_fmt", "yuv420p",
                "-r", f"{self.fps:.8g}",
                "-c:a", self.audio_codec,
                "-b:a", self.audio_bitrate,
                "-ar", "48000",
                "-ac", "2",
                "-movflags", "+faststart",
                "-t", f"{duration:.6f}",
                str(output_path),
            ]
        )
        run_process(argv)
        probe = probe_media(output_path, self.ffprobe)
        actual = media_duration(probe)
        if actual is None or abs(actual - duration) > max(0.1, 2.0 / self.fps):
            raise ToolError(f"{segment_id}: rendered duration {actual!r} does not match {duration:.3f}s")
        self.segment_records.append(
            {
                "segment_id": segment_id,
                "output": str(output_path),
                "sha256": sha256_file(output_path),
                "duration_seconds": actual,
                "speed_factor": speed,
                "disclosures": disclosure_strings(segment),
                "command": json_safe_command(argv),
                "source_assets": [str(item[2]) for item in resolved_refs],
                "source_audio_used": source_audio_input is not None,
                "narration_audio_used": narration_input is not None,
            }
        )
        return output_path

    def concatenate(self, segments: list[Path], output: Path, subtitles: Path | None, burn: bool) -> list[list[str]]:
        planned = self.storyboard.get("segments", [])
        if not segments or len(segments) != len(planned):
            raise ToolError("Final assembly requires one rendered file per storyboard segment")
        durations = [segment_times(segment)[2] for segment in planned]
        total_duration = sum(durations)
        argv = [self.ffmpeg, "-hide_banner", "-loglevel", "warning", "-y"]
        for path in segments:
            argv.extend(["-i", str(path)])

        # AAC packet padding must not advance the next segment's video clock.
        # Trim decoded streams to their planned intervals before concatenation.
        graph: list[str] = []
        for index, duration in enumerate(durations):
            graph.append(f"[{index}:v]trim=duration={duration:.6f},setpts=PTS-STARTPTS[v{index}]")
            graph.append(
                f"[{index}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"apad,atrim=duration={duration:.6f},asetpts=PTS-STARTPTS[a{index}]"
            )
        inputs = "".join(f"[v{index}][a{index}]" for index in range(len(segments)))
        graph.append(f"{inputs}concat=n={len(segments)}:v=1:a=1[vconcat][aconcat]")
        video_filters = [
            f"[vconcat]fps={self.fps:.8g}:start_time=0",
            f"trim=duration={total_duration:.6f}",
            "setpts=PTS-STARTPTS",
        ]
        output.parent.mkdir(parents=True, exist_ok=True)
        staged = output.parent / f".{output.stem}.rendering{output.suffix}"
        if staged.exists():
            staged.unlink()
        if burn:
            if subtitles is None or not subtitles.is_file():
                raise ToolError("--burn-subtitles requires an existing --subtitles file")
            style = self.storyboard.get("style") if isinstance(self.storyboard.get("style"), dict) else {}
            force_style = "FontSize=20,Outline=2,Shadow=0,MarginV=42"
            if "subtitle_font_size" in style:
                # SRT uses libass' default 384x288 script space. Convert requested output pixels.
                subtitle_size = max(12.0, number(style.get("subtitle_font_size"), self.height * 0.044))
                margin = max(0.03, number(style.get("subtitle_margin_fraction"), 0.05))
                family = str(style.get("subtitle_font_name", ""))
                if not family and str(self.storyboard.get("project", {}).get("language", "")).startswith("zh"):
                    family = "Microsoft YaHei" if os.name == "nt" else "Noto Sans CJK SC"
                if any(char in family for char in "',:;\\"):
                    raise ToolError("subtitle_font_name contains unsupported filter characters")
                force_style = (
                    f"FontSize={subtitle_size * 288 / self.height:.4f},"
                    f"Outline={max(0.8, subtitle_size * 0.03 * 288 / self.height):.4f},"
                    f"Shadow=0,BorderStyle=3,BackColour=&H88000000,"
                    f"MarginV={margin * 288:.4f},MarginL={margin * 384:.4f},MarginR={margin * 384:.4f}"
                )
                if family:
                    force_style += f",FontName={family}"
            video_filters.append(f"subtitles=filename='{escape_filter_path(subtitles)}':force_style='{force_style}'")
        video_filters.append("format=yuv420p[vout]")
        graph.append(",".join(video_filters))
        graph.append(f"[aconcat]atrim=duration={total_duration:.6f},asetpts=PTS-STARTPTS[aout]")
        argv.extend([
            "-filter_complex", ";".join(graph), "-map", "[vout]", "-map", "[aout]",
            "-c:v", self.video_codec, "-preset", self.preset, "-crf", str(self.crf),
            "-pix_fmt", "yuv420p", "-r", f"{self.fps:.8g}",
            "-c:a", self.audio_codec, "-b:a", self.audio_bitrate, "-ar", "48000", "-ac", "2",
            "-t", f"{total_duration:.6f}", "-movflags", "+faststart", str(staged),
        ])
        run_process(argv)
        os.replace(staged, output)
        return [json_safe_command(argv)]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render storyboard.json to a normalized MP4 with FFmpeg.")
    parser.add_argument("storyboard", type=Path)
    parser.add_argument("--output", type=Path, default=Path("academic_video.mp4"))
    parser.add_argument("--profile", type=Path, help="Recorded in the manifest; compliance is enforced by qa_video.py.")
    parser.add_argument("--subtitles", type=Path)
    parser.add_argument("--burn-subtitles", action="store_true")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    parser.add_argument("--font-file", type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--keep-work", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = args.output.resolve()
    if output.exists() and not args.overwrite:
        print(f"NOT AUDITABLE: output exists; pass --overwrite to replace it: {output}", file=sys.stderr)
        return 2
    try:
        ffmpeg = resolve_tool(args.ffmpeg, "ffmpeg")
        ffprobe = resolve_tool(args.ffprobe, "ffprobe")
        storyboard_path = args.storyboard.resolve()
        storyboard = load_json(storyboard_path)
        mode_errors = academic_mode_findings(storyboard)
        if mode_errors:
            raise ToolError(mode_errors[0]["message"])
        timeline_errors = [item for item in timeline_findings(storyboard) if item.get("severity") == "P0"]
        if timeline_errors:
            raise ToolError("Storyboard timeline is invalid: " + "; ".join(item["message"] for item in timeline_errors))
        language = str(storyboard.get("project", {}).get("language", "en"))
        font = args.font_file.resolve() if args.font_file else find_default_font(language)
        if font and not font.is_file():
            raise ToolError(f"Font file does not exist: {font}")
        temporary: tempfile.TemporaryDirectory[str] | None = None
        if args.work_dir:
            work_dir = args.work_dir.resolve()
            work_dir.mkdir(parents=True, exist_ok=True)
        else:
            temporary = tempfile.TemporaryDirectory(prefix="academic-video-render-")
            work_dir = Path(temporary.name)
        renderer = Renderer(storyboard_path, storyboard, ffmpeg, ffprobe, font, work_dir)
        segment_paths = [renderer.render_segment(segment, index) for index, segment in enumerate(storyboard["segments"])]
        final_commands = renderer.concatenate(
            segment_paths,
            output,
            args.subtitles.resolve() if args.subtitles else None,
            args.burn_subtitles,
        )
        final_probe = probe_media(output, ffprobe)
        manifest_path = (args.manifest or output.with_name("render_manifest.json")).resolve()
        version = run_process([ffmpeg, "-version"]).stdout.splitlines()[0]
        manifest = {
            "schema_version": "1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "storyboard": str(storyboard_path),
            "storyboard_sha256": sha256_file(storyboard_path),
            "conference_profile": str(args.profile.resolve()) if args.profile else None,
            "conference_profile_sha256": sha256_file(args.profile.resolve()) if args.profile and args.profile.is_file() else None,
            "ffmpeg": ffmpeg,
            "ffmpeg_version": version,
            "ffprobe": ffprobe,
            "font_file": str(font) if font else None,
            "output": str(output),
            "output_sha256": sha256_file(output),
            "output_duration_seconds": media_duration(final_probe),
            "subtitles": str(args.subtitles.resolve()) if args.subtitles else None,
            "subtitles_burned": args.burn_subtitles,
            "segments": renderer.segment_records,
            "final_commands": final_commands,
            "status": "PASS",
        }
        atomic_write_json(manifest_path, manifest)
        if args.keep_work and temporary is not None:
            retained = output.parent / f"{output.stem}_render_work"
            if retained.exists():
                raise ToolError(f"Cannot retain work directory because destination exists: {retained}")
            shutil.copytree(work_dir, retained)
        if temporary is not None:
            temporary.cleanup()
        print(f"PASS: rendered {len(segment_paths)} segment(s) to {output}")
        print(f"Manifest: {manifest_path}")
        return 0
    except (OSError, ValueError, ToolError, KeyError) as exc:
        print(f"NOT AUDITABLE: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
