#!/usr/bin/env python3
"""Automated compliance, integrity, and academic-integrity QA for rendered video."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from video_common import (
    ToolError,
    academic_mode_findings,
    asset_index,
    atomic_write_json,
    atomic_write_text,
    disclosure_strings,
    first_stream,
    load_json,
    media_duration,
    normalize_container,
    parse_timecode,
    probe_media,
    rational_to_float,
    resolve_asset_path,
    resolve_tool,
    run_process,
    segment_times,
    sha256_file,
    timeline_findings,
)


@dataclass
class Finding:
    id: str
    severity: str
    automated: bool
    message: str
    evidence: Any
    remediation: str


def add(
    findings: list[Finding],
    finding_id: str,
    severity: str,
    message: str,
    evidence: Any,
    remediation: str,
    *,
    automated: bool = True,
) -> None:
    findings.append(Finding(finding_id, severity, automated, message, evidence, remediation))


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def contains_any(texts: list[str], needles: list[str]) -> bool:
    corpus = " | ".join(normalize_text(value) for value in texts)
    return any(normalize_text(needle) in corpus for needle in needles)


def constraint_value(constraints: dict[str, Any], key: str) -> Any:
    value = constraints.get(key)
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def allowed_values(constraints: dict[str, Any], key: str) -> list[Any]:
    value = constraints.get(key)
    if isinstance(value, dict):
        candidate = value.get("allowed", value.get("values", value.get("value")))
    else:
        candidate = value
    if candidate is None:
        return []
    return candidate if isinstance(candidate, list) else [candidate]


def expected_duration(storyboard: dict[str, Any]) -> float | None:
    segments = storyboard.get("segments")
    if not isinstance(segments, list) or not segments:
        return None
    try:
        return max(segment_times(segment)[1] for segment in segments if isinstance(segment, dict))
    except (ValueError, TypeError):
        return None


def parse_srt_timestamp(value: str) -> float:
    return parse_timecode(value.strip().replace(",", "."))


def audit_subtitles(path: Path, video_duration: float, findings: list[Finding]) -> list[dict[str, Any]]:
    if not path.is_file():
        add(findings, "SUBTITLE_MISSING", "P0", "Subtitle file is missing.", str(path), "Generate subtitles.srt from timed narration.")
        return []
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    blocks = [block.strip() for block in re.split(r"\n{2,}", text) if block.strip()]
    cues: list[dict[str, Any]] = []
    previous_end = 0.0
    for position, block in enumerate(blocks, 1):
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].strip().isdigit() or "-->" not in lines[1]:
            add(findings, "SUBTITLE_TIMESTAMP_ERROR", "P0", f"Invalid SRT block {position}.", block[:200], "Regenerate the SRT with make_subtitles.py.")
            continue
        try:
            raw_start, raw_end = lines[1].split("-->", 1)
            start = parse_srt_timestamp(raw_start)
            end = parse_srt_timestamp(raw_end)
        except (ValueError, TypeError) as exc:
            add(findings, "SUBTITLE_TIMESTAMP_ERROR", "P0", f"Invalid timestamp in SRT block {position}.", str(exc), "Regenerate the SRT with make_subtitles.py.")
            continue
        body = lines[2:]
        if end <= start or start < previous_end - 0.001 or end > video_duration + 0.05:
            add(
                findings,
                "SUBTITLE_TIMESTAMP_ERROR",
                "P0",
                f"Subtitle timing is invalid at cue {position}.",
                {"start": start, "end": end, "previous_end": previous_end, "video_duration": video_duration},
                "Fix overlap, ordering, or out-of-range timestamps.",
            )
        joined = " ".join(body).strip()
        cps = len(re.sub(r"\s+", "", joined)) / max(end - start, 0.001)
        if len(body) > 2 or any(len(line) > 52 for line in body) or cps > 22:
            add(
                findings,
                "SUBTITLE_READABILITY_RISK",
                "P1",
                f"Subtitle cue {position} may be too dense.",
                {"lines": len(body), "max_characters": max((len(line) for line in body), default=0), "cps": round(cps, 2)},
                "Shorten or split the cue; target no more than two concise lines.",
            )
        cues.append({"index": position, "start": start, "end": end, "text": joined})
        previous_end = max(previous_end, end)
    if not cues:
        add(findings, "SUBTITLE_TIMESTAMP_ERROR", "P0", "No valid subtitle cues were found.", str(path), "Regenerate the SRT from timed narration.")
    return cues


def audit_storyboard(
    storyboard_path: Path,
    storyboard: dict[str, Any],
    findings: list[Finding],
    manifest: dict[str, Any] | None,
) -> None:
    for item in academic_mode_findings(storyboard):
        add(findings, item["id"], item["severity"], item["message"], None, "Choose an academic mode before rendering.")
    for item in timeline_findings(storyboard):
        add(findings, item["id"], item["severity"], item["message"], None, "Repair storyboard segment timing before rendering.")
    assets = asset_index(storyboard)
    resolved: dict[str, Path] = {}
    for asset_id, asset in assets.items():
        try:
            path = resolve_asset_path(storyboard_path, asset)
            resolved[asset_id] = path
            if not path.is_file():
                add(findings, "MISSING_ASSET", "P0", f"Asset {asset_id} is missing.", str(path), "Restore the source asset or update storyboard.json.")
            expected_hash = asset.get("sha256")
            if path.is_file() and expected_hash and expected_hash != sha256_file(path):
                add(findings, "ASSET_HASH_MISMATCH", "P0", f"Asset {asset_id} changed after evidence mapping.", str(path), "Re-inventory and review the changed asset before rendering.")
        except ToolError as exc:
            add(findings, "MISSING_ASSET", "P0", str(exc), asset_id, "Add a valid path for every asset.")

    project = storyboard.get("project") if isinstance(storyboard.get("project"), dict) else {}
    voiceover_required = project.get("voiceover") in {True, "yes", "required"}
    manifest_segments = {}
    if manifest and isinstance(manifest.get("segments"), list):
        manifest_segments = {
            str(item.get("segment_id")): item
            for item in manifest["segments"]
            if isinstance(item, dict)
        }
    for segment in storyboard.get("segments", []) or []:
        if not isinstance(segment, dict):
            continue
        segment_id = str(segment.get("segment_id", "<unknown>"))
        narration = segment.get("narration") if isinstance(segment.get("narration"), dict) else {}
        intentional_silence = narration.get("required") is False and bool(str(narration.get("reason", "")).strip())
        if voiceover_required and not intentional_silence and not str(narration.get("text", "")).strip():
            add(findings, "MISSING_NARRATION", "P0", f"{segment_id} has no narration text.", segment_id, "Write source-grounded narration or explicitly set voiceover to no.")
        overlays = disclosure_strings(segment)
        refs = segment.get("source_asset", [])
        if isinstance(refs, dict):
            refs = [refs]
        refs = refs if isinstance(refs, list) else []
        referenced_assets = [assets.get(str(ref.get("asset_id"))) for ref in refs if isinstance(ref, dict)]
        is_simulation = any(
            asset
            and (
                asset.get("is_simulation") is True
                or "simulation" in str(asset.get("evidence_class", "")).casefold()
            )
            for asset in referenced_assets
        )
        if is_simulation and not contains_any(overlays, ["simulation", "simulated", "仿真", "模拟"]):
            add(findings, "UNLABELLED_SIMULATION", "P0", f"{segment_id} presents simulation without an explicit label.", overlays, "Show 'Simulation' for the full segment.")
        representative = any(
            asset and str(asset.get("trial_selection", "")).casefold() == "representative"
            for asset in referenced_assets
        )
        if representative and not contains_any(overlays, ["representative trial", "代表性试验", "代表性实验"]):
            add(findings, "UNLABELLED_REPRESENTATIVE_TRIAL", "P0", f"{segment_id} uses a representative trial without disclosure.", overlays, "Show 'Representative trial' for the full segment.")
        speed = float(segment.get("speed_factor", 1.0) or 1.0)
        if abs(speed - 1.0) > 1e-6:
            factor_tokens = [f"{speed:g}×", f"{speed:g}x"]
            if not contains_any(overlays, ["playback", "real time", "slow motion", "播放", *factor_tokens]):
                add(findings, "UNLABELLED_SPEED_CHANGE", "P0", f"{segment_id} changes playback speed without a clear disclosure.", {"speed_factor": speed, "overlays": overlays}, "Show 'Playback: N× real time' or an equivalent full-segment label.")
        processing = segment.get("processing") if isinstance(segment.get("processing"), dict) else {}
        for key, labels, finding_id in (
            ("interpolation", ["interpolation", "插帧"], "UNLABELLED_INTERPOLATION"),
            ("stabilization", ["stabilized", "stabilization", "防抖", "稳定处理"], "UNLABELLED_STABILIZATION"),
        ):
            if processing.get(key) and not contains_any(overlays, labels):
                add(findings, finding_id, "P0", f"{segment_id} uses {key} without disclosure.", overlays, f"Label {key} for the affected segment.")

        manifest_item = manifest_segments.get(segment_id)
        if manifest is not None and manifest_item is None:
            add(findings, "RENDER_MANIFEST_SEGMENT_MISSING", "P0", f"{segment_id} is absent from render_manifest.json.", segment_id, "Render again from the current storyboard.")
        elif manifest_item is not None:
            applied = [str(item) for item in manifest_item.get("disclosures", [])]
            for required in overlays:
                if normalize_text(required) not in {normalize_text(item) for item in applied}:
                    add(findings, "DISCLOSURE_NOT_RENDERED", "P0", f"{segment_id} disclosure is not evidenced by the render manifest.", required, "Render again and retain the generated render_manifest.json.")

        visual = segment.get("visual") if isinstance(segment.get("visual"), dict) else {}
        layout = str(visual.get("layout", "single"))
        if layout in {"side_by_side", "split_horizontal", "top_bottom", "split_vertical"}:
            labels = [str(ref.get("label", "")).strip() for ref in refs if isinstance(ref, dict)]
            contract = visual.get("comparison_contract") if isinstance(visual.get("comparison_contract"), dict) else {}
            if len(refs) < 2 or any(not label for label in labels[:2]):
                add(findings, "COMPARISON_LABEL_RISK", "P1", f"{segment_id} comparison lacks clear labels.", labels, "Label both compared conditions.")
            if not contract or not contract.get("synchronized") or not contract.get("matched_speed"):
                add(findings, "COMPARISON_FAIRNESS_RISK", "P1", f"{segment_id} does not record synchronized, matched-speed comparison conditions.", contract, "Document a shared clock, matched speed, and matched crop or explain the exception.")

        claim = segment.get("claim")
        if isinstance(claim, dict) and str(claim.get("kind", "")).casefold() == "quantitative":
            missing = [key for key in ("metric", "units", "sample_size", "baseline") if not claim.get(key)]
            if missing:
                add(findings, "QUANTITATIVE_CONTEXT_MISSING", "P1", f"{segment_id} quantitative claim lacks context.", missing, "Show metric, units, sample size, and baseline where applicable.")

        text = str(narration.get("text", "")).strip()
        if text:
            try:
                duration = segment_times(segment)[2]
            except (ValueError, TypeError):
                duration = 1.0
            chinese = len(re.findall(r"[\u3400-\u9fff]", text))
            if chinese:
                rate = chinese / max(duration, 0.001) * 60
                too_fast = rate > 280
                unit = "Chinese characters/min"
            else:
                rate = len(re.findall(r"\b[\w'-]+\b", text)) / max(duration, 0.001) * 60
                too_fast = rate > 170
                unit = "words/min"
            if too_fast:
                add(findings, "NARRATION_TOO_FAST", "P1", f"{segment_id} narration may be too fast.", {"rate": round(rate, 1), "unit": unit}, "Shorten the narration or lengthen the segment.")

        for overlay in segment.get("overlay_text", []) or []:
            if not isinstance(overlay, dict):
                continue
            style = storyboard.get("style") if isinstance(storyboard.get("style"), dict) else {}
            output = storyboard.get("output") if isinstance(storyboard.get("output"), dict) else {}
            height = int(output.get("height", (output.get("resolution") or {}).get("height", 1080)))
            font_size = float(overlay.get("font_size", style.get("overlay_font_size", height * 0.04)))
            if font_size < height * 0.026:
                add(findings, "TEXT_TOO_SMALL", "P1", f"{segment_id} overlay text may be too small.", {"font_size": font_size, "frame_height": height}, "Use at least about 2.6% of frame height.")
            box = overlay.get("box") if isinstance(overlay.get("box"), dict) else {}
            if box and any(float(box.get(key, 0)) < 0.03 for key in ("x", "y")):
                add(findings, "SAFE_MARGIN_RISK", "P1", f"{segment_id} overlay is close to the frame edge.", box, "Keep critical text inside a 5% title-safe margin.")


def audit_profile(
    video: Path,
    probe: dict[str, Any],
    profile: dict[str, Any],
    subtitle_path: Path | None,
    findings: list[Finding],
) -> None:
    status = str(profile.get("verification_status", "unverified")).casefold()
    if status not in {"verified", "test"}:
        add(findings, "PROFILE_UNVERIFIED", "P1", "Conference requirements are not verified against an official source.", status, "Verify the active year/type on an official conference, publisher, or journal page.")
    if status == "verified" and not profile.get("official_sources"):
        add(findings, "PROFILE_SOURCE_MISSING", "P0", "A verified profile has no official source record.", None, "Record official URL/PDF, locator, and checked date.")
    constraints = profile.get("constraints") if isinstance(profile.get("constraints"), dict) else {}
    audit_constraints(video, probe, constraints, subtitle_path, findings)


def audit_constraints(
    video: Path,
    probe: dict[str, Any],
    constraints: dict[str, Any],
    subtitle_path: Path | None,
    findings: list[Finding],
) -> None:
    duration = media_duration(probe)
    max_duration = constraint_value(constraints, "max_duration")
    if max_duration is not None and duration is not None and duration > float(max_duration) + 0.02:
        add(findings, "DURATION_LIMIT_EXCEEDED", "P0", "Video exceeds the profile duration limit.", {"actual": duration, "maximum": max_duration}, "Shorten the timeline and re-render with a two-frame safety margin.")
    max_size = constraint_value(constraints, "max_file_size")
    if max_size is not None and video.stat().st_size > int(max_size):
        add(findings, "FILE_SIZE_LIMIT_EXCEEDED", "P0", "Video exceeds the profile file-size limit.", {"actual": video.stat().st_size, "maximum": max_size}, "Reduce bitrate or duration without changing scientific content.")
    actual_container = normalize_container((probe.get("format") or {}).get("format_name"), video.suffix)
    containers = [str(item).casefold().lstrip(".") for item in allowed_values(constraints, "container")]
    if containers and str(actual_container).casefold() not in containers:
        add(findings, "CONTAINER_NONCOMPLIANT", "P0", "Container does not match the profile.", {"actual": actual_container, "allowed": containers}, "Render to an allowed container.")
    video_stream = first_stream(probe, "video")
    audio_stream = first_stream(probe, "audio")
    if video_stream:
        codecs = [str(item).casefold() for item in allowed_values(constraints, "video_codec")]
        actual_codec = str(video_stream.get("codec_name", "")).casefold()
        aliases = {"avc": "h264", "avc1": "h264", "x264": "h264", "libx264": "h264"}
        codecs = [aliases.get(item, item) for item in codecs]
        actual_codec = aliases.get(actual_codec, actual_codec)
        if codecs and actual_codec not in codecs:
            add(findings, "CODEC_NONCOMPLIANT", "P0", "Video codec does not match the profile.", {"actual": actual_codec, "allowed": codecs}, "Encode with an allowed codec.")
        resolution = constraints.get("resolution")
        allowed_resolutions: list[dict[str, Any]] = []
        if isinstance(resolution, dict):
            candidate = resolution.get("allowed", resolution.get("value"))
            allowed_resolutions = candidate if isinstance(candidate, list) else ([candidate] if isinstance(candidate, dict) else [])
        if allowed_resolutions and not any(
            int(item.get("width", -1)) == int(video_stream.get("width", -2))
            and int(item.get("height", -1)) == int(video_stream.get("height", -2))
            for item in allowed_resolutions
        ):
            add(findings, "RESOLUTION_NONCOMPLIANT", "P0", "Resolution does not match the profile.", {"actual": [video_stream.get("width"), video_stream.get("height")], "allowed": allowed_resolutions}, "Render at an allowed resolution.")
        fps_values = [float(item) for item in allowed_values(constraints, "fps")]
        actual_fps = rational_to_float(video_stream.get("avg_frame_rate"))
        if fps_values and (actual_fps is None or not any(abs(actual_fps - value) < 0.02 for value in fps_values)):
            add(findings, "FPS_NONCOMPLIANT", "P0", "Frame rate does not match the profile.", {"actual": actual_fps, "allowed": fps_values}, "Render to a constant allowed frame rate.")
    audio_requirement = str(constraints.get("audio_requirement", "unknown")).casefold()
    if audio_requirement == "required" and audio_stream is None:
        add(findings, "AUDIO_MISSING", "P0", "Profile requires audio but the video has no audio stream.", None, "Add narration or an explicitly allowed audio track.")
    caption_requirement = str(constraints.get("caption_requirement", "unknown")).casefold()
    if caption_requirement == "required" and (subtitle_path is None or not subtitle_path.is_file()):
        add(findings, "CAPTIONS_MISSING", "P0", "Profile requires captions but subtitles.srt is missing.", None, "Generate and include captions.")


def ffmpeg_diagnostics(
    video: Path,
    ffmpeg: str,
    duration: float,
    audio_required: bool,
    storyboard: dict[str, Any],
    findings: list[Finding],
) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {}
    decode = run_process([ffmpeg, "-v", "error", "-xerror", "-i", str(video), "-f", "null", "-"], check=False)
    diagnostics["decode_returncode"] = decode.returncode
    diagnostics["decode_stderr"] = decode.stderr[-4000:]
    if decode.returncode != 0 or decode.stderr.strip():
        add(findings, "CORRUPTED_FRAMES", "P0", "Full video decode reported an error.", decode.stderr[-1000:], "Inspect the encode and render again from source media.")

    black = run_process(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(video), "-vf", "blackdetect=d=0.50:pix_th=0.10", "-an", "-f", "null", "-"],
        check=False,
    )
    black_intervals = [
        {"start": float(start), "end": float(end), "duration": float(length)}
        for start, end, length in re.findall(
            r"black_start:([0-9.]+)\s+black_end:([0-9.]+)\s+black_duration:([0-9.]+)", black.stderr
        )
    ]
    diagnostics["black_intervals"] = black_intervals
    for interval in black_intervals:
        if interval["duration"] >= 1.0:
            add(findings, "UNEXPECTED_BLACK_FRAME", "P1", "A sustained black interval was detected.", interval, "Confirm it is intentional; otherwise repair the source or transition.")

    probe_audio = run_process(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(video), "-af", "silencedetect=noise=-50dB:d=1", "-vn", "-f", "null", "-"],
        check=False,
    )
    starts = [float(value) for value in re.findall(r"silence_start:\s*([0-9.]+)", probe_audio.stderr)]
    ends = [(float(end), float(length)) for end, length in re.findall(r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)", probe_audio.stderr)]
    diagnostics["silence_starts"] = starts
    diagnostics["silence_ends"] = ends
    full_silence = any(
        start <= 0.1 and end >= duration - 0.1
        for start, (end, _) in zip(starts, ends)
    )
    if full_silence and audio_required:
        add(findings, "SILENT_REQUIRED_AUDIO", "P0", "The required audio track is effectively silent.", {"duration": duration}, "Add audible narration or correct the mix.")
    elif any(length >= 5.0 for _, length in ends):
        add(findings, "LONG_SILENCE", "P1", "A long silent interval was detected.", ends, "Confirm that the silence is intentional and does not indicate a missing narration segment.")

    volume = run_process(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(video), "-af", "volumedetect", "-vn", "-f", "null", "-"],
        check=False,
    )
    maximum_match = re.search(r"max_volume:\s*(-?(?:inf|[0-9.]+))\s*dB", volume.stderr, re.IGNORECASE)
    maximum = None
    if maximum_match and maximum_match.group(1).casefold() != "-inf":
        maximum = float(maximum_match.group(1))
        if maximum >= -0.05:
            add(findings, "AUDIO_CLIPPING", "P0", "Audio peak reaches clipping risk.", {"max_volume_db": maximum}, "Lower the mix and re-render with a limiter and headroom.")
    diagnostics["max_volume_db"] = maximum

    freeze = run_process(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(video), "-vf", "freezedetect=n=-60dB:d=5", "-an", "-f", "null", "-"],
        check=False,
    )
    freeze_durations = [float(value) for value in re.findall(r"lavfi\.freezedetect\.freeze_duration:\s*([0-9.]+)", freeze.stderr)]
    diagnostics["freeze_durations"] = freeze_durations
    allow_long_static = all(
        isinstance(segment, dict)
        and (
            (isinstance(segment.get("visual"), dict) and segment["visual"].get("allow_static"))
            or str(segment.get("role", "")).casefold() in {"title", "end_card"}
        )
        for segment in storyboard.get("segments", []) or []
    )
    if freeze_durations and not allow_long_static:
        add(findings, "LONG_STATIC_SEGMENT", "P1", "A long static video interval was detected.", freeze_durations, "Confirm a declared freeze/title card or add evidence-bearing motion.")
    return diagnostics


def audit_visual_review(video: Path, review_path: Path | None, findings: list[Finding]) -> None:
    if review_path is None or not review_path.is_file():
        add(findings, "VISUAL_REVIEW_PENDING", "P1", "Manual visual review is not recorded for this render.", None, "Inspect representative frames for occlusion, readability, color accessibility, labels, and anonymization; save visual_review.json bound to the video SHA-256.", automated=False)
        return
    review = load_json(review_path)
    actual_hash = sha256_file(video)
    if review.get("video_sha256") != actual_hash:
        add(findings, "VISUAL_REVIEW_STALE", "P1", "Visual review does not match the current video hash.", {"review": review.get("video_sha256"), "actual": actual_hash}, "Repeat visual review on the current render.", automated=False)
        return
    required = ["subject_visible", "plot_readable", "labels_clear", "color_accessible", "anonymization_checked"]
    missing = [key for key in required if review.get("checks", {}).get(key) is not True]
    if review.get("status") != "PASS" or missing:
        add(findings, "VISUAL_REVIEW_FAILED", "P1", "Manual visual review has unresolved checks.", {"missing": missing, "status": review.get("status")}, "Resolve and re-review the current video.", automated=False)


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Video QA report",
        "",
        f"- Status: **{report['status']}**",
        f"- Video: `{report['video']}`",
        f"- SHA-256: `{report['video_sha256']}`",
        f"- Generated: {report['generated_at']}",
        f"- P0 blockers: {report['summary']['p0_count']}",
        f"- P1 warnings: {report['summary']['p1_count']}",
        "",
        "## Measured output",
        "",
        f"- Duration: {report['measured']['duration_seconds']} s",
        f"- Resolution: {report['measured']['resolution']}",
        f"- FPS: {report['measured']['fps']}",
        f"- Video codec: {report['measured']['video_codec']}",
        f"- Audio codec: {report['measured']['audio_codec']}",
        f"- Container: {report['measured']['container']}",
        f"- File size: {report['measured']['file_size_bytes']} bytes",
        "",
        "## Findings",
        "",
    ]
    if not report["findings"]:
        lines.append("No P0 or P1 findings.")
    else:
        for finding in report["findings"]:
            kind = "automated" if finding["automated"] else "manual"
            lines.extend(
                [
                    f"### {finding['severity']} — {finding['id']}",
                    "",
                    f"{finding['message']} ({kind})",
                    "",
                    f"- Evidence: `{json.dumps(finding['evidence'], ensure_ascii=False)}`",
                    f"- Remediation: {finding['remediation']}",
                    "",
                ]
            )
    lines.extend(
        [
            "## Gate semantics",
            "",
            "`FAIL` means at least one P0 blocker. `WARN` means no P0 but one or more P1 findings. "
            "`PASS` means the automated contract and the hash-bound visual review both passed.",
            "",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit a rendered academic video and emit PASS/WARN/FAIL reports.")
    parser.add_argument("video", type=Path)
    parser.add_argument("--storyboard", type=Path, required=True)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--subtitles", type=Path)
    parser.add_argument("--render-manifest", type=Path)
    parser.add_argument("--visual-review", type=Path)
    parser.add_argument("--output-md", type=Path, default=Path("qa_report.md"))
    parser.add_argument("--output-json", type=Path, default=Path("qa_report.json"))
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    parser.add_argument("--fail-on-warn", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        ffmpeg = resolve_tool(args.ffmpeg, "ffmpeg")
        ffprobe = resolve_tool(args.ffprobe, "ffprobe")
        video = args.video.resolve()
        if not video.is_file():
            raise ToolError(f"Rendered video does not exist: {video}")
        storyboard_path = args.storyboard.resolve()
        storyboard = load_json(storyboard_path)
        manifest = load_json(args.render_manifest.resolve()) if args.render_manifest else None
        profile = load_json(args.profile.resolve()) if args.profile else None
        if args.inventory:
            inventory = load_json(args.inventory.resolve())
            if inventory.get("errors"):
                inventory_errors = inventory["errors"]
            else:
                inventory_errors = []
        else:
            inventory_errors = []
        probe = probe_media(video, ffprobe)
        duration = media_duration(probe)
        if duration is None:
            raise ToolError("ffprobe could not determine output duration")
        findings: list[Finding] = []
        for error in inventory_errors:
            add(findings, "MISSING_ASSET", "P0", "Media inventory contains an input error.", error, "Repair and regenerate media_inventory.json.")
        audit_storyboard(storyboard_path, storyboard, findings, manifest)
        planned = expected_duration(storyboard)
        if planned is not None and abs(duration - planned) > 0.12:
            add(findings, "OUTPUT_DURATION_MISMATCH", "P0", "Rendered duration differs from storyboard duration.", {"actual": duration, "planned": planned}, "Render again from the current storyboard.")
        subtitle_path = args.subtitles.resolve() if args.subtitles else None
        if subtitle_path is not None:
            audit_subtitles(subtitle_path, duration, findings)
        if profile is not None:
            audit_profile(video, probe, profile, subtitle_path, findings)
        active_profile = profile or {}
        constraints = active_profile.get("constraints", {})
        audio_required = str(constraints.get("audio_requirement", "unknown")).casefold() == "required"
        diagnostics = ffmpeg_diagnostics(video, ffmpeg, duration, audio_required, storyboard, findings)
        audit_visual_review(video, args.visual_review.resolve() if args.visual_review else None, findings)

        p0 = [item for item in findings if item.severity == "P0"]
        p1 = [item for item in findings if item.severity == "P1"]
        status = "FAIL" if p0 else ("WARN" if p1 else "PASS")
        video_stream = first_stream(probe, "video") or {}
        audio_stream = first_stream(probe, "audio") or {}
        report = {
            "schema_version": "1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "video": str(video),
            "video_sha256": sha256_file(video),
            "inputs": {
                "storyboard": str(storyboard_path),
                "inventory": str(args.inventory.resolve()) if args.inventory else None,
                "profile": str(args.profile.resolve()) if args.profile else None,
                "subtitles": str(subtitle_path) if subtitle_path else None,
                "render_manifest": str(args.render_manifest.resolve()) if args.render_manifest else None,
                "visual_review": str(args.visual_review.resolve()) if args.visual_review else None,
            },
            "measured": {
                "duration_seconds": round(duration, 6),
                "resolution": [video_stream.get("width"), video_stream.get("height")],
                "fps": rational_to_float(video_stream.get("avg_frame_rate")),
                "video_codec": video_stream.get("codec_name"),
                "audio_codec": audio_stream.get("codec_name"),
                "audio_sample_rate": audio_stream.get("sample_rate"),
                "container": normalize_container((probe.get("format") or {}).get("format_name"), video.suffix),
                "file_size_bytes": video.stat().st_size,
            },
            "diagnostics": diagnostics,
            "summary": {"p0_count": len(p0), "p1_count": len(p1), "finding_count": len(findings)},
            "findings": [asdict(item) for item in findings],
        }
        atomic_write_json(args.output_json.resolve(), report)
        atomic_write_text(args.output_md.resolve(), markdown_report(report))
        print(f"{status}: {len(p0)} P0 blocker(s), {len(p1)} P1 warning(s).")
        print(args.output_md.resolve())
        if status == "FAIL":
            return 1
        if status == "WARN" and args.fail_on_warn:
            return 1
        return 0
    except (OSError, ValueError, TypeError, ToolError, json.JSONDecodeError) as exc:
        print(f"NOT AUDITABLE: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
