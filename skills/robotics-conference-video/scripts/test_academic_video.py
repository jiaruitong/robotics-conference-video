#!/usr/bin/env python3
"""Independent academic-video gates and a 24-second, entirely synthetic render.

Fixtures are constructed here without reading templates, papers, or user media.
The generated tone is a test signal, not spoken narration or experimental audio.
Visual review stays pending until someone actually inspects the exported frames.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import inspect_media
import make_subtitles
import qa_video
import render_video
from video_common import (
    ACADEMIC_MODES,
    academic_mode_findings,
    atomic_write_json,
    atomic_write_text,
    load_json,
    media_duration,
    probe_media,
    run_process,
    sha256_file,
    timeline_findings,
)


ARTIFACTS_DIRECTORY: Path | None = None
RUN_MEDIA_TESTS = True


def temporary_fixture(prefix: str = "academic-video-test-") -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(prefix=prefix)


def synthetic_profile() -> dict:
    return {
        "schema_version": "1.0",
        "conference": "",
        "year": None,
        "mode": "supplementary",
        "verification_status": "test",
        "official_sources": [],
        "notes": "Synthetic Test A: pipeline validation only; no submission rules are asserted.",
        "constraints": {
            "max_duration": {"value": 30},
            "max_file_size": {"value": 50_000_000},
            "container": {"allowed": ["mp4"]},
            "video_codec": {"allowed": ["h264"]},
            "resolution": {"allowed": [{"width": 640, "height": 360}]},
            "fps": {"values": [24]},
            "audio_requirement": "required",
            "caption_requirement": "required",
        },
    }


def synthetic_storyboard() -> dict:
    return {
        "schema_version": "1.0",
        "project": {
            "title": "Academic Video Example",
            "mode": "supplementary",
            "language": "en",
            "voiceover": "no",
            "subtitles": "yes",
        },
        "output": {"width": 640, "height": 360, "fps": 24, "encoder": "libx264",
                   "audio_encoder": "aac", "preset": "ultrafast", "crf": 22},
        "audio": {"use_source_audio": False},
        "style": {"overlay_font_size": 24, "subtitle_font_size": 22},
        "assets": [],
        "claims": [],
        "segments": [{
            "segment_id": "S001", "start_time": 0, "end_time": 4, "duration": 4,
            "role": "title", "source_asset": [], "overlay_text": [],
            "narration": {"text": "Academic Video Example"},
            "visual": {"layout": "single", "background_color": "0x283443", "allow_static": True},
        }],
    }


def synthetic_probe() -> dict:
    return {
        "format": {"duration": "24", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 640,
             "height": 360, "avg_frame_rate": "24/1"},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }


def finding_ids(findings: list[qa_video.Finding], severity: str | None = None) -> set[str]:
    return {item.id for item in findings if severity is None or item.severity == severity}


def write_synthetic_png(path: Path, width: int, height: int, *, transparent: bool = False) -> None:
    """Write constructed RGBA pixels using only the Python standard library."""
    pixels = bytearray()
    bars = (1, 3, 2, 4)
    for y in range(height):
        pixels.append(0)
        for x in range(width):
            color = (0, 0, 0, 0) if transparent else (40, 52, 68, 255)
            if transparent:
                if width // 3 <= x < width * 2 // 3 and height // 3 <= y < height * 2 // 3:
                    color = (240, 120, 20, 255)
            elif width >= 200:
                if x == width // 8 or y == height * 7 // 8:
                    color = (215, 225, 235, 255)
                for index, value in enumerate(bars):
                    left = width * (2 + index * 2) // 11
                    right = left + width // 11
                    if left <= x < right and height * 7 // 8 - value * height // 7 <= y < height * 7 // 8:
                        color = (40 + index * 35, 175, 215 - index * 25, 255)
            pixels.extend(color)

    def chunk(kind: bytes, value: bytes) -> bytes:
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value) & 0xFFFFFFFF)

    content = b"\x89PNG\r\n\x1a\n"
    content += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    content += chunk(b"IDAT", zlib.compress(bytes(pixels))) + chunk(b"IEND", b"")
    path.write_bytes(content)


class AcademicGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = synthetic_profile()
        self.storyboard = synthetic_storyboard()
        self.probe = synthetic_probe()

    def audit(self, manifest: dict | None = None) -> list[qa_video.Finding]:
        findings: list[qa_video.Finding] = []
        with temporary_fixture() as directory:
            qa_video.audit_storyboard(Path(directory) / "synthetic-storyboard.json", self.storyboard, findings, manifest)
        return findings

    def test_only_explicit_academic_modes_are_valid(self) -> None:
        self.assertEqual(ACADEMIC_MODES, {"supplementary", "presentation", "teaser"})
        for mode in ACADEMIC_MODES:
            self.storyboard["project"]["mode"] = mode
            self.assertEqual(academic_mode_findings(self.storyboard), [])
        for mode in (None, "invalid", [], {}):
            self.storyboard["project"]["mode"] = mode
            self.assertIn("ACADEMIC_MODE_INVALID", finding_ids(self.audit(), "P0"))

    def test_synthetic_profile_does_not_claim_official_verification(self) -> None:
        findings: list[qa_video.Finding] = []
        profile = {"verification_status": "test", "official_sources": [], "constraints": {}}
        qa_video.audit_profile(Path("synthetic.mp4"), self.probe, profile, None, findings)
        self.assertEqual(findings, [])

    def test_unverified_requirements_warn(self) -> None:
        findings: list[qa_video.Finding] = []
        qa_video.audit_profile(Path("synthetic.mp4"), self.probe,
                               {"verification_status": "unverified", "constraints": {}}, None, findings)
        self.assertIn("PROFILE_UNVERIFIED", finding_ids(findings, "P1"))

    def test_verified_requirements_need_an_official_source(self) -> None:
        findings: list[qa_video.Finding] = []
        qa_video.audit_profile(Path("synthetic.mp4"), self.probe,
                               {"verification_status": "verified", "constraints": {}}, None, findings)
        self.assertIn("PROFILE_SOURCE_MISSING", finding_ids(findings, "P0"))

    def test_constraints_accept_matching_measured_output(self) -> None:
        with temporary_fixture() as directory:
            video, subtitles = Path(directory) / "synthetic.mp4", Path(directory) / "subtitles.srt"
            video.write_bytes(b"Synthetic Test A")
            subtitles.write_text("1\n00:00:00,000 --> 00:00:04,000\nSynthetic Test A\n", encoding="utf-8")
            findings: list[qa_video.Finding] = []
            qa_video.audit_profile(video, self.probe, self.profile, subtitles, findings)
            self.assertEqual(findings, [])

    def test_all_measured_constraint_mismatches_remain_blockers(self) -> None:
        with temporary_fixture() as directory:
            video = Path(directory) / "synthetic.mp4"
            video.write_bytes(b"Synthetic Test A")
            constraints = self.profile["constraints"]
            constraints.update(max_duration={"value": 23}, max_file_size={"value": 1},
                               container={"allowed": ["webm"]}, video_codec={"allowed": ["vp9"]},
                               resolution={"allowed": [{"width": 800, "height": 600}]}, fps={"values": [60]})
            self.probe["streams"] = self.probe["streams"][:1]
            findings: list[qa_video.Finding] = []
            qa_video.audit_profile(video, self.probe, self.profile, None, findings)
            self.assertEqual(finding_ids(findings, "P0"), {
                "DURATION_LIMIT_EXCEEDED", "FILE_SIZE_LIMIT_EXCEEDED", "CONTAINER_NONCOMPLIANT",
                "CODEC_NONCOMPLIANT", "RESOLUTION_NONCOMPLIANT", "FPS_NONCOMPLIANT",
                "AUDIO_MISSING", "CAPTIONS_MISSING",
            })

    def test_timeline_gaps_overlaps_and_duplicates_remain_blockers(self) -> None:
        for start, segment_id, expected in ((3, "S002", "TIMELINE_OVERLAP"),
                                            (5, "S002", "TIMELINE_GAP"),
                                            (4, "S001", "DUPLICATE_SEGMENT_ID")):
            with self.subTest(expected=expected):
                storyboard = copy.deepcopy(self.storyboard)
                storyboard["segments"].append({"segment_id": segment_id, "start_time": start,
                                                "end_time": start + 4, "duration": 4})
                self.assertIn(expected, {item["id"] for item in timeline_findings(storyboard)})

    def test_missing_assets_and_changed_hashes_remain_blockers(self) -> None:
        with temporary_fixture() as directory:
            source = Path(directory) / "synthetic.bin"
            self.storyboard["assets"] = [{"asset_id": "A001", "path": str(source)}]
            self.assertIn("MISSING_ASSET", finding_ids(self.audit(), "P0"))
            source.write_bytes(b"Synthetic Test A")
            self.storyboard["assets"][0]["sha256"] = "0" * 64
            self.assertIn("ASSET_HASH_MISMATCH", finding_ids(self.audit(), "P0"))

    def test_evidence_disclosures_and_manifest_remain_required(self) -> None:
        with temporary_fixture() as directory:
            source = Path(directory) / "synthetic.bin"
            source.write_bytes(b"Synthetic Test A")
            self.storyboard["assets"] = [{"asset_id": "A001", "path": str(source),
                                          "is_simulation": True, "trial_selection": "representative"}]
            segment = self.storyboard["segments"][0]
            segment.update(source_asset=[{"asset_id": "A001"}], speed_factor=2,
                           processing={"interpolation": True, "stabilization": True})
            self.assertEqual(finding_ids(self.audit(), "P0"), {
                "UNLABELLED_SIMULATION", "UNLABELLED_REPRESENTATIVE_TRIAL", "UNLABELLED_SPEED_CHANGE",
                "UNLABELLED_INTERPOLATION", "UNLABELLED_STABILIZATION",
            })
            labels = ["Simulation", "Representative trial", "Playback: 2x real time", "Interpolation", "Stabilized"]
            segment["overlay_text"] = labels
            manifest = {"segments": [{"segment_id": "S001", "disclosures": labels}]}
            self.assertEqual(self.audit(manifest), [])
            manifest["segments"][0]["disclosures"] = []
            self.assertIn("DISCLOSURE_NOT_RENDERED", finding_ids(self.audit(manifest), "P0"))
            self.assertIn("RENDER_MANIFEST_SEGMENT_MISSING", finding_ids(self.audit({"segments": []}), "P0"))

    def test_comparison_and_quantitative_context_remain_visible(self) -> None:
        segment = self.storyboard["segments"][0]
        segment.update(source_asset=[{"asset_id": "A001"}, {"asset_id": "A002"}],
                       visual={"layout": "side_by_side"}, claim={"kind": "quantitative"})
        self.assertEqual(finding_ids(self.audit(), "P1"), {
            "COMPARISON_LABEL_RISK", "COMPARISON_FAIRNESS_RISK", "QUANTITATIVE_CONTEXT_MISSING",
        })
        segment["source_asset"][0]["label"] = "Synthetic Test A"
        segment["source_asset"][1]["label"] = "Synthetic Test B"
        segment["visual"]["comparison_contract"] = {"synchronized": True, "matched_speed": True}
        segment["claim"].update(metric="Synthetic index", units="arbitrary units", sample_size=4, baseline="Constructed values")
        self.assertEqual(self.audit(), [])

    def test_intentional_silence_requires_an_explanation(self) -> None:
        self.storyboard["project"]["voiceover"] = "required"
        segment = self.storyboard["segments"][0]
        segment["narration"] = {"required": False}
        self.assertIn("MISSING_NARRATION", finding_ids(self.audit(), "P0"))
        segment["narration"]["reason"] = "Intentional silent observation interval"
        self.assertNotIn("MISSING_NARRATION", finding_ids(self.audit(), "P0"))

    def test_narration_density_and_overlay_legibility_warn(self) -> None:
        segment = self.storyboard["segments"][0]
        segment["narration"]["text"] = "Synthetic text " * 20
        segment["overlay_text"] = [{"text": "Synthetic Test A", "font_size": 5, "box": {"x": 0, "y": 0}}]
        self.assertEqual(finding_ids(self.audit(), "P1"), {"NARRATION_TOO_FAST", "TEXT_TOO_SMALL", "SAFE_MARGIN_RISK"})

    def test_srt_preserves_precise_timestamps_and_neutral_bilingual_text(self) -> None:
        cues = [
            make_subtitles.Cue("S001", 0.125, 2.5, "Academic Video Example", ["Academic Video", "Example"], 22, 9),
            make_subtitles.Cue("S002", 2.5, 4.75, "合成测试字幕。", ["合成测试字幕。"], 14, 4),
        ]
        self.assertEqual(make_subtitles.render_srt(cues),
                         "1\n00:00:00,125 --> 00:00:02,500\nAcademic Video\nExample\n\n"
                         "2\n00:00:02,500 --> 00:00:04,750\n合成测试字幕。\n")

    def test_subtitle_order_range_and_density_are_checked(self) -> None:
        with temporary_fixture() as directory:
            subtitle = Path(directory) / "subtitles.srt"
            subtitle.write_text("1\n00:00:00,000 --> 00:00:02,000\nSynthetic Test A\n\n"
                                "2\n00:00:01,000 --> 00:00:05,000\n" + "A" * 100 + "\n", encoding="utf-8")
            findings: list[qa_video.Finding] = []
            qa_video.audit_subtitles(subtitle, 4, findings)
            self.assertIn("SUBTITLE_TIMESTAMP_ERROR", finding_ids(findings, "P0"))
            self.assertIn("SUBTITLE_READABILITY_RISK", finding_ids(findings, "P1"))

    def test_visual_review_is_pending_or_stale_until_hash_bound(self) -> None:
        with temporary_fixture() as directory:
            video, review = Path(directory) / "synthetic.mp4", Path(directory) / "visual_review.json"
            video.write_bytes(b"Synthetic Test A")
            findings: list[qa_video.Finding] = []
            qa_video.audit_visual_review(video, None, findings)
            self.assertIn("VISUAL_REVIEW_PENDING", finding_ids(findings, "P1"))
            atomic_write_json(review, {"status": "PASS", "video_sha256": "0" * 64, "checks": {}})
            findings = []
            qa_video.audit_visual_review(video, review, findings)
            self.assertIn("VISUAL_REVIEW_STALE", finding_ids(findings, "P1"))
            atomic_write_json(review, {"status": "PASS", "video_sha256": sha256_file(video), "checks": {}})
            findings = []
            qa_video.audit_visual_review(video, review, findings)
            self.assertIn("VISUAL_REVIEW_FAILED", finding_ids(findings, "P1"))

    def test_corruption_silence_black_clipping_and_freeze_gates(self) -> None:
        responses = [
            subprocess.CompletedProcess([], 1, "", "Synthetic decode failure"),
            subprocess.CompletedProcess([], 0, "", "black_start:0 black_end:2 black_duration:2"),
            subprocess.CompletedProcess([], 0, "", "silence_start: 0\nsilence_end: 24 | silence_duration: 24"),
            subprocess.CompletedProcess([], 0, "", "max_volume: 0.0 dB"),
            subprocess.CompletedProcess([], 0, "", "lavfi.freezedetect.freeze_duration: 6"),
        ]
        self.storyboard["segments"][0]["role"] = "evidence"
        self.storyboard["segments"][0]["visual"]["allow_static"] = False
        findings: list[qa_video.Finding] = []
        with patch.object(qa_video, "run_process", side_effect=responses):
            qa_video.ffmpeg_diagnostics(Path("synthetic.mp4"), "ffmpeg", 24, True, self.storyboard, findings)
        self.assertEqual(finding_ids(findings, "P0"), {"CORRUPTED_FRAMES", "SILENT_REQUIRED_AUDIO", "AUDIO_CLIPPING"})
        self.assertEqual(finding_ids(findings, "P1"), {"UNEXPECTED_BLACK_FRAME", "LONG_STATIC_SEGMENT"})


class SyntheticRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        if not RUN_MEDIA_TESTS:
            self.skipTest("Media rendering omitted by --unit-only")
        self.ffmpeg = os.environ.get("ACADEMIC_VIDEO_FFMPEG") or shutil.which("ffmpeg")
        self.ffprobe = os.environ.get("ACADEMIC_VIDEO_FFPROBE") or shutil.which("ffprobe")
        if not self.ffmpeg or not self.ffprobe:
            self.skipTest("Put FFmpeg/ffprobe on PATH or set ACADEMIC_VIDEO_FFMPEG/ACADEMIC_VIDEO_FFPROBE")

    def generate_card(self, path: Path, *, alternate: bool = False) -> None:
        pattern = "testsrc" if alternate else "testsrc2"
        run_process([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                     f"{pattern}=size=320x240:rate=24:duration=12", "-f", "lavfi", "-i",
                     "sine=frequency=660:sample_rate=48000:duration=12", "-c:v", "libx264",
                     "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-threads", "1", "-c:a", "aac", str(path)])

    def test_final_clock_ignores_synthetic_audio_packet_tail(self) -> None:
        with temporary_fixture("academic-video-tail-test-") as directory:
            folder = Path(directory)
            sources = []
            colors = ("0x283443", "0x435228")
            for index, color in enumerate(colors):
                path = folder / f"synthetic_tail_{index}.mp4"
                run_process([
                    self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"color=c={color}:s=128x96:r=24:d=1",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1.25",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-threads", "1", "-c:a", "aac", str(path),
                ])
                self.assertGreater(media_duration(probe_media(path, self.ffprobe)), 1.2)
                sources.append(path)
            storyboard = synthetic_storyboard()
            storyboard["output"].update(width=128, height=96)
            storyboard["style"]["subtitle_font_size"] = 12
            storyboard["segments"] = [
                {"segment_id": "S001", "start_time": 0, "end_time": 1, "duration": 1},
                {"segment_id": "S002", "start_time": 1, "end_time": 2, "duration": 1},
            ]
            subtitles = folder / "subtitles.srt"
            atomic_write_text(subtitles, "1\n00:00:00,000 --> 00:00:02,000\nSynthetic Test A\n")
            renderer = render_video.Renderer(folder / "storyboard.json", storyboard, self.ffmpeg, self.ffprobe, None, folder)
            for burn in (False, True):
                with self.subTest(burn=burn):
                    video = folder / f"synthetic_clock_{int(burn)}.mp4"
                    renderer.concatenate(sources, video, subtitles if burn else None, burn)
                    measured = probe_media(video, self.ffprobe)
                    self.assertAlmostEqual(media_duration(measured), 2.0, delta=0.02)
                    probe = json.loads(run_process([
                        self.ffprobe, "-v", "error", "-select_streams", "v:0", "-count_frames",
                        "-show_streams", "-show_frames", "-of", "json", str(video),
                    ]).stdout)
                    self.assertEqual(probe["streams"][0]["avg_frame_rate"], "24/1")
                    timestamps = [float(frame["best_effort_timestamp_time"]) for frame in probe["frames"]]
                    self.assertEqual(len(timestamps), 48)
                    for index, timestamp in enumerate(timestamps):
                        self.assertAlmostEqual(timestamp, index / 24, places=5)
                    decoded = subprocess.run([
                        self.ffmpeg, "-v", "error", "-i", str(video), "-an", "-f", "rawvideo",
                        "-pix_fmt", "rgb24", "pipe:1",
                    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout
                    frame_bytes = 128 * 96 * 3
                    self.assertEqual(len(decoded), 48 * frame_bytes)
                    for index in range(48):
                        offset = index * frame_bytes + (40 * 128 + 64) * 3
                        expected = (40, 52, 68) if index < 24 else (67, 82, 40)
                        self.assertTrue(all(abs(actual - value) < 15 for actual, value in zip(decoded[offset:offset + 3], expected)))

    def test_24_second_synthetic_render_and_full_qa(self) -> None:
        context = contextlib.nullcontext(str(ARTIFACTS_DIRECTORY)) if ARTIFACTS_DIRECTORY else temporary_fixture("academic-video-render-test-")
        with context as directory:
            folder = Path(directory)
            card_a, card_b = folder / "test_card_a.mp4", folder / "test_card_b.mp4"
            figure, graphic, tone = folder / "synthetic_values.png", folder / "synthetic_overlay.png", folder / "test_tone.wav"
            self.generate_card(card_a)
            self.generate_card(card_b, alternate=True)
            write_synthetic_png(figure, 320, 240)
            write_synthetic_png(graphic, 60, 60, transparent=True)
            run_process([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                         "sine=frequency=440:sample_rate=48000:duration=24", "-af", "volume=0.5", str(tone)])

            storyboard = synthetic_storyboard()
            storyboard["assets"] = [
                {"asset_id": "A001", "path": card_a.name, "kind": "video", "is_simulation": True,
                 "evidence_class": "synthetic_test", "provenance": "FFmpeg lavfi testsrc2"},
                {"asset_id": "A002", "path": card_b.name, "kind": "video", "is_simulation": True,
                 "evidence_class": "synthetic_test", "provenance": "FFmpeg lavfi testsrc"},
                {"asset_id": "A003", "path": figure.name, "kind": "figure", "is_simulation": True,
                 "evidence_class": "synthetic_test", "provenance": "Constructed pixels for values 1, 3, 2, 4"},
                {"asset_id": "G001", "path": graphic.name, "kind": "image", "provenance": "Constructed RGBA pixels"},
                {"asset_id": "N001", "path": tone.name, "kind": "audio", "provenance": "FFmpeg lavfi sine; not speech"},
            ]
            for asset in storyboard["assets"]:
                asset["sha256"] = sha256_file(folder / asset["path"])
            storyboard["claims"] = [{"claim_id": "C001", "kind": "descriptive",
                                      "text": "The fixture exercises video operations using generated media only.",
                                      "source_asset_ids": ["A001", "A002", "A003"],
                                      "evidence_scope": "Pipeline test; no research result or empirical capability claim."}]
            cues = ["Academic Video Example", "Synthetic Test A: generated test card.",
                    "Two synchronized synthetic inputs.", "Constructed values: one, three, two, four.",
                    "Synthetic playback at twice real time.", "合成测试：仅用于验证视频流程。"]
            storyboard["segments"] = []
            for index, text in enumerate(cues):
                segment = {
                    "segment_id": f"S{index + 1:03d}", "start_time": index * 4,
                    "end_time": (index + 1) * 4, "duration": 4, "speed_factor": 1,
                    "role": "title" if index == 0 else ("end_card" if index == 5 else "evidence"),
                    "claim_id": "C001", "source_asset": [],
                    "narration": {"text": text, "audio_asset_id": "N001", "in_time": index * 4,
                                  "audio_note": "Sine test signal; this is not a spoken voiceover."},
                    "overlay_text": [{"text": "Academic Video Example" if index == 0 else "Synthetic Test A",
                                      "x": 0.06, "y": 0.06, "font_size": 24}],
                    "visual": {"layout": "single", "background_color": "0x283443", "allow_static": True},
                }
                if index in {1, 2, 3, 4}:
                    segment["overlay_text"].append({"text": "Simulation / synthetic fixture", "x": 0.06, "y": 0.18, "font_size": 18})
                if index == 1:
                    segment["source_asset"] = [{"asset_id": "A001", "in_time": 1, "out_time": 5}]
                elif index == 2:
                    segment["source_asset"] = [{"asset_id": "A001", "in_time": 2, "out_time": 6, "label": "Synthetic Test A"},
                                               {"asset_id": "A002", "in_time": 2, "out_time": 6, "label": "Synthetic Test B"}]
                    segment["visual"].update(layout="side_by_side", comparison_contract={"synchronized": True, "matched_speed": True, "matched_crop": True})
                    segment["overlay_text"] = [{"text": "Simulation / synthetic fixture", "x": 0.06, "y": 0.20, "font_size": 18}]
                elif index == 3:
                    segment["source_asset"] = [{"asset_id": "A003"}]
                    segment["visual"]["overlays"] = [{"asset_id": "G001", "x": 0.80, "y": 0.32, "w": 60, "h": 60}]
                    segment["overlay_text"].append({"text": "Synthetic values: 1, 3, 2, 4", "x": 0.06, "y": 0.30, "font_size": 18})
                elif index == 4:
                    segment["source_asset"] = [{"asset_id": "A001", "in_time": 1, "out_time": 9},
                                               {"asset_id": "A002", "in_time": 1, "out_time": 9}]
                    segment["speed_factor"] = 2
                    segment["visual"].update(layout="picture_in_picture", pip_width=0.30, pip_x=0.65, pip_y=0.40)
                    segment["overlay_text"].append({"text": "Playback: 2x real time", "x": 0.06, "y": 0.30, "font_size": 18})
                else:
                    segment["visual"]["boxes"] = [{"x": 0.18, "y": 0.40, "w": 0.64, "h": 0.20, "color": "cyan@0.8", "thickness": 3}]
                storyboard["segments"].append(segment)

            storyboard_path, profile_path = folder / "storyboard.json", folder / "conference_profile.json"
            atomic_write_json(storyboard_path, storyboard)
            atomic_write_json(profile_path, synthetic_profile())
            atomic_write_json(folder / "evidence_map.json", {"project": "Academic Video Example", "test_only": True,
                              "claims": storyboard["claims"], "assets": [{"asset_id": asset["asset_id"], "provenance": asset["provenance"], "sha256": asset["sha256"]} for asset in storyboard["assets"]]})
            narration = folder / "narration.md"
            atomic_write_text(narration, "\n".join(f"## S{index + 1:03d} [{index * 4} --> {(index + 1) * 4}]\n{text}\n" for index, text in enumerate(cues)))
            subtitles = folder / "subtitles.srt"
            self.assertEqual(make_subtitles.main([str(narration), "--output", str(subtitles), "--strict"]), 0)
            inventory = folder / "media_inventory.json"
            self.assertEqual(inspect_media.main([str(card_a), str(card_b), str(figure), str(graphic), str(tone),
                                                "--output", str(inventory), "--ffprobe", self.ffprobe, "--strict"]), 0)
            video, manifest = folder / "academic_video.mp4", folder / "render_manifest.json"
            self.assertEqual(render_video.main([str(storyboard_path), "--profile", str(profile_path),
                                               "--subtitles", str(subtitles), "--burn-subtitles", "--output", str(video),
                                               "--manifest", str(manifest), "--work-dir", str(folder / "working"),
                                               "--ffmpeg", self.ffmpeg, "--ffprobe", self.ffprobe]), 0)
            report = folder / "qa_report.json"
            qa_args = [str(video), "--storyboard", str(storyboard_path), "--profile", str(profile_path),
                       "--inventory", str(inventory), "--subtitles", str(subtitles), "--render-manifest", str(manifest),
                       "--output-md", str(folder / "qa_report.md"), "--output-json", str(report),
                       "--ffmpeg", self.ffmpeg, "--ffprobe", self.ffprobe]
            self.assertEqual(qa_video.main(qa_args), 0)
            audit = load_json(report)
            self.assertEqual(audit["status"], "WARN")
            self.assertEqual(audit["summary"]["p0_count"], 0)
            self.assertEqual({item["id"] for item in audit["findings"]}, {"VISUAL_REVIEW_PENDING"})
            self.assertEqual(audit["measured"]["resolution"], [640, 360])
            self.assertEqual(audit["measured"]["fps"], 24)
            self.assertEqual(audit["measured"]["video_codec"], "h264")
            self.assertEqual(audit["measured"]["audio_codec"], "aac")
            self.assertEqual(audit["diagnostics"]["decode_returncode"], 0)
            self.assertAlmostEqual(audit["measured"]["duration_seconds"], 24, delta=0.12)
            applied = load_json(manifest)
            self.assertTrue(applied["subtitles_burned"])
            self.assertEqual(applied["conference_profile_sha256"], sha256_file(profile_path))
            self.assertTrue(all(not item["source_audio_used"] and item["narration_audio_used"] for item in applied["segments"]))
            commands = json.dumps(applied["segments"], ensure_ascii=False)
            for operation in ("trim=", "scale=", "pad=", "overlay=", "drawtext=", "hstack=", "setpts=PTS/2"):
                self.assertIn(operation, commands)
            self.assertIn("concat", json.dumps(applied["final_commands"]))
            self.assertIn("subtitles=", json.dumps(applied["final_commands"]))

            # Measure frame spacing as well as the metadata's advertised frame rate.
            frames = json.loads(run_process([self.ffprobe, "-v", "error", "-select_streams", "v:0",
                                            "-show_entries", "frame=best_effort_timestamp_time", "-show_frames", "-of", "json", str(video)]).stdout)["frames"]
            timestamps = [float(frame["best_effort_timestamp_time"]) for frame in frames]
            self.assertEqual(len(timestamps), 24 * 24)
            for first, second in zip(timestamps, timestamps[1:]):
                self.assertAlmostEqual(second - first, 1 / 24, delta=0.0001)
            for timestamp in (2, 6, 10, 14, 18, 22):
                run_process([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(timestamp),
                             "-i", str(video), "-frames:v", "1", "-threads", "1", str(folder / f"review_frame_{timestamp:02d}.png")])
            atomic_write_json(folder / "visual_review.pending.json", {"status": "PENDING", "video_sha256": sha256_file(video),
                              "checks": {}, "note": "Inspect exported frames and video before recording a manual review."})

            # Negative QA uses the same actual render; it does not encode a second video.
            wrong = synthetic_profile()
            wrong["constraints"].update(max_duration={"value": 23}, max_file_size={"value": 1},
                                         container={"allowed": ["webm"]}, video_codec={"allowed": ["vp9"]},
                                         resolution={"allowed": [{"width": 800, "height": 600}]}, fps={"values": [60]})
            wrong_path = folder / "negative_profile.json"
            atomic_write_json(wrong_path, wrong)
            negative_args = list(qa_args)
            negative_args[negative_args.index("--profile") + 1] = str(wrong_path)
            negative_args[negative_args.index("--output-md") + 1] = str(folder / "qa_negative.md")
            negative_report = folder / "qa_negative.json"
            negative_args[negative_args.index("--output-json") + 1] = str(negative_report)
            self.assertEqual(qa_video.main(negative_args), 1)
            negative = load_json(negative_report)
            self.assertEqual({item["id"] for item in negative["findings"] if item["severity"] == "P0"}, {
                "DURATION_LIMIT_EXCEEDED", "FILE_SIZE_LIMIT_EXCEEDED", "CONTAINER_NONCOMPLIANT",
                "CODEC_NONCOMPLIANT", "RESOLUTION_NONCOMPLIANT", "FPS_NONCOMPLIANT",
            })

    def test_static_pixels_and_transparent_overlays_preserve_every_frame(self) -> None:
        with temporary_fixture("academic-video-clock-test-") as directory:
            folder = Path(directory)
            figure, graphic, motion = folder / "synthetic_figure.png", folder / "synthetic_overlay.png", folder / "synthetic_motion.mp4"
            write_synthetic_png(figure, 128, 96)
            write_synthetic_png(graphic, 30, 30, transparent=True)
            run_process([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                         "color=c=0x283443:s=128x96:r=30:d=5,drawbox=x=96:y=64:w=24:h=20:color=magenta:t=fill:enable='gte(t,1)'",
                         "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1", str(motion)])
            for fps in (24, 30):
                for duration in (1.3, 2.4, 4.7):
                    for kind in ("image", "figure", "generated_card", "video"):
                        with self.subTest(fps=fps, duration=duration, kind=kind):
                            work = folder / f"{fps}-{duration}-{kind}"
                            work.mkdir()
                            storyboard = synthetic_storyboard()
                            storyboard["output"].update(width=128, height=96, fps=fps, crf=18)
                            storyboard["assets"] = [{"asset_id": "A001", "path": str(motion if kind == "video" else figure), "kind": kind},
                                                     {"asset_id": "G001", "path": str(graphic), "kind": "image"}]
                            segment = {"segment_id": "S001", "start_time": 0, "end_time": duration, "duration": duration,
                                       "source_asset": [{"asset_id": "A001"}], "visual": {"overlays": [
                                           {"asset_id": "G001", "x": 30, "y": 18, "w": 30, "h": 30}]}}
                            renderer = render_video.Renderer(folder / "storyboard.json", storyboard, self.ffmpeg, self.ffprobe, None, work)
                            output = renderer.render_segment(segment, 0)
                            probe = json.loads(run_process([self.ffprobe, "-v", "error", "-select_streams", "v:0", "-count_frames",
                                                            "-show_streams", "-show_frames", "-of", "json", str(output)]).stdout)
                            self.assertEqual(probe["streams"][0]["avg_frame_rate"], f"{fps}/1")
                            timestamps = [float(frame["best_effort_timestamp_time"]) for frame in probe["frames"]]
                            self.assertLess(abs(len(timestamps) - duration * fps), 1.0)
                            for index, timestamp in enumerate(timestamps):
                                self.assertAlmostEqual(timestamp, index / fps, places=5)
                            decoded = subprocess.run([self.ffmpeg, "-v", "error", "-i", str(output), "-an", "-f", "rawvideo",
                                                      "-pix_fmt", "rgb24", "pipe:1"], stdout=subprocess.PIPE,
                                                     stderr=subprocess.PIPE, check=True).stdout
                            frame_bytes = 128 * 96 * 3
                            self.assertEqual(len(decoded), len(timestamps) * frame_bytes)
                            for index, timestamp in enumerate(timestamps):
                                def pixel(x: int, y: int) -> tuple[int, ...]:
                                    offset = index * frame_bytes + (y * 128 + x) * 3
                                    return tuple(decoded[offset:offset + 3])
                                orange = pixel(45, 33)
                                self.assertGreater(orange[0], 200)
                                self.assertTrue(80 < orange[1] < 160)
                                self.assertLess(orange[2], 70)
                                background = pixel(34, 22)
                                self.assertTrue(all(abs(actual - expected) < 15 for actual, expected in zip(background, (40, 52, 68))))
                                if kind == "video":
                                    event = pixel(108, 74)
                                    if timestamp < 0.95:
                                        self.assertLess(max(event), 100)
                                    elif timestamp > 1.05:
                                        self.assertGreater(event[0], 200)
                                        self.assertGreater(event[2], 200)
                                        self.assertLess(event[1], 60)
                            command = renderer.segment_records[0]["command"]
                            input_rates = [command[index + 1] for index, token in enumerate(command) if token == "-framerate"]
                            self.assertEqual(input_rates, ["1"] if kind == "video" else ["1", "1"])
                            self.assertEqual(command[command.index("-r") + 1], str(fps))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--unit-only", action="store_true", help="Run deterministic gates without encoding media.")
    parser.add_argument("--artifacts-dir", type=Path, help="Retain the 24-second render and reports in a new or empty directory.")
    options, test_args = parser.parse_known_args()
    RUN_MEDIA_TESTS = not options.unit_only
    if options.artifacts_dir:
        ARTIFACTS_DIRECTORY = options.artifacts_dir.resolve()
        if ARTIFACTS_DIRECTORY.exists() and any(ARTIFACTS_DIRECTORY.iterdir()):
            raise SystemExit("Artifact directory must be new or empty; existing files are never silently replaced")
        ARTIFACTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[sys.argv[0], *test_args], verbosity=2)
