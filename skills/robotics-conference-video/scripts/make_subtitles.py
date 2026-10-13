#!/usr/bin/env python3
"""Convert strictly timed narration Markdown into deterministic SRT subtitles."""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from video_common import ToolError, atomic_write_json, atomic_write_text, format_timecode, parse_timecode


HEADER = re.compile(
    r"^##\s+(?P<id>[^\s\[]+)\s+\[(?P<start>[^\]]+?)\s*-->\s*(?P<end>[^\]]+?)\]\s*$"
)


@dataclass
class Cue:
    segment_id: str
    start: float
    end: float
    text: str
    lines: list[str]
    cells: int
    cps: float


def cell_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1 for char in text)


def split_units(text: str, language: str) -> list[str]:
    if language == "zh" or (language == "auto" and any("\u3400" <= ch <= "\u9fff" for ch in text)):
        return list(text)
    return re.findall(r"\S+\s*", text)


def wrap_text(text: str, language: str, max_cells: int) -> list[str]:
    normalized = re.sub(r"\s+", " ", text.strip())
    if not normalized:
        return []
    lines: list[str] = []
    current = ""
    for unit in split_units(normalized, language):
        candidate = current + unit
        if current and cell_width(candidate.rstrip()) > max_cells:
            lines.append(current.rstrip())
            current = unit.lstrip()
        else:
            current = candidate
    if current.strip():
        lines.append(current.strip())
    return lines


def parse_markdown(path: Path, language: str, max_cells: int) -> list[Cue]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    cues: list[Cue] = []
    index = 0
    while index < len(lines):
        raw = lines[index].strip()
        if not raw:
            index += 1
            continue
        match = HEADER.match(raw)
        if not match:
            raise ToolError(f"Expected timed heading at line {index + 1}: {lines[index]}")
        start = parse_timecode(match.group("start"))
        end = parse_timecode(match.group("end"))
        if end <= start:
            raise ToolError(f"Cue {match.group('id')} has a non-positive duration")
        index += 1
        body: list[str] = []
        while index < len(lines) and not HEADER.match(lines[index].strip()):
            if lines[index].strip():
                body.append(lines[index].strip())
            index += 1
        text = " ".join(body).strip()
        if not text:
            raise ToolError(f"Cue {match.group('id')} has no narration text")
        wrapped = wrap_text(text, language, max_cells)
        cues.append(
            Cue(
                segment_id=match.group("id"),
                start=start,
                end=end,
                text=text,
                lines=wrapped,
                cells=cell_width(text),
                cps=len(re.sub(r"\s+", "", text)) / (end - start),
            )
        )
    if not cues:
        raise ToolError("No timed narration cues were found")
    return cues


def validate(cues: list[Cue], max_lines: int) -> list[str]:
    errors: list[str] = []
    previous_end = 0.0
    seen: set[str] = set()
    for cue in cues:
        if cue.segment_id in seen:
            errors.append(f"duplicate segment id: {cue.segment_id}")
        seen.add(cue.segment_id)
        if cue.start < previous_end - 0.001:
            errors.append(f"subtitle overlap at {cue.segment_id}")
        if len(cue.lines) > max_lines:
            errors.append(
                f"{cue.segment_id} needs {len(cue.lines)} lines (limit {max_lines}); split or shorten narration"
            )
        previous_end = max(previous_end, cue.end)
    return errors


def render_srt(cues: list[Cue]) -> str:
    blocks: list[str] = []
    for number, cue in enumerate(cues, 1):
        subtitle_text = "\n".join(cue.lines)
        blocks.append(
            f"{number}\n"
            f"{format_timecode(cue.start, decimal=',')} --> {format_timecode(cue.end, decimal=',')}\n"
            f"{subtitle_text}"
        )
    return "\n\n".join(blocks) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert headings like '## S001 [00:00:00.000 --> 00:00:04.000]' into SRT."
    )
    parser.add_argument("input", type=Path, help="Timed narration Markdown.")
    parser.add_argument("--output", type=Path, default=Path("subtitles.srt"))
    parser.add_argument("--language", choices=("auto", "en", "zh"), default="auto")
    parser.add_argument("--max-lines", type=int, default=2)
    parser.add_argument("--max-cells-per-line", type=int, default=42)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--strict", action="store_true", help="Do not write SRT when wrapping or timeline checks fail.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cues = parse_markdown(args.input.resolve(), args.language, args.max_cells_per_line)
        errors = validate(cues, args.max_lines)
    except (OSError, ValueError, ToolError) as exc:
        print(f"NOT AUDITABLE: {exc}", file=sys.stderr)
        return 2

    report = {
        "schema_version": "1.0",
        "input": str(args.input.resolve()),
        "output": str(args.output.resolve()),
        "cue_count": len(cues),
        "duration_seconds": max(cue.end for cue in cues),
        "errors": errors,
        "cues": [asdict(cue) for cue in cues],
        "status": "FAIL" if errors else "PASS",
    }
    if args.report_json:
        atomic_write_json(args.report_json.resolve(), report)
    if errors and args.strict:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    atomic_write_text(args.output.resolve(), render_srt(cues))
    if errors:
        for error in errors:
            print(f"WARN: {error}", file=sys.stderr)
    print(f"{'WARN' if errors else 'PASS'}: wrote {len(cues)} subtitle cue(s) to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
