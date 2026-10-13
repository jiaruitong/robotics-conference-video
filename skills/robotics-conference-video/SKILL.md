---
name: robotics-conference-video
description: Create, edit, render, and quality-check source-grounded academic videos from supplied papers and authorized research media. Use for paper supplementary videos, conference presentations, and academic paper teasers, including 论文补充视频、学术会议报告视频 and 学术 teaser.
---

# Academic Conference Video

Use scientific evidence first, clear academic storytelling second, visual clarity third. The installation identifier is retained for compatibility; this is a field-independent workflow.

Announce use of this Skill. Never invent a source, finding, measurement, requirement, or observed event.

## Progressive loading

Read `manifest.yaml`, `references/workflow.md`, and `references/qa-contract.md`.

Load only relevant supporting guidance:

- venue requirements: `references/conference-requirements.md`
- evidence selection, comparisons, and data views: `references/evidence-patterns.md`
- narrative and narration: `references/storytelling.md`
- text, figures, captions, and layout: `references/visual-style.md`
- rendering and troubleshooting: `references/ffmpeg-recipes.md`

## Intake and supported modes

Inspect supplied materials before asking questions. Ask only for missing choices that affect submission requirements, scientific meaning, or available evidence: venue/year/type, duration, language, narration/captions, source media, resolution, and anonymization.

Only `supplementary`, `presentation`, and `teaser` are accepted. Use an explicit mode in every storyboard.

Working defaults are 16:9, 1920×1080, 30 fps, H.264/AAC, external SRT, and no music. They are not conference requirements. User choices and verified venue rules override them.

- `supplementary`: evidence, evaluation conditions, comparisons, and relevant boundaries.
- `presentation`: research question → method → evidence → conclusion and limitations.
- `teaser`: central supported finding, minimum context, and material caveats.

## Source-grounded pipeline

Create a separate local output directory for each project.

1. Read the supplied paper and authorized material. Write `paper_claims.md` with claim IDs, locators, conditions, findings, and limitations.
2. Build `evidence_map.md`: claim → source item or data → exact range or locator → caveat.
3. Author `storyboard.json` and synchronized `storyboard.md`. Use `assets/storyboard.template.json` as an empty intake skeleton, not a ready-made example.
4. Write narration in the requested language. Record actual speech timing in `narration_timed.md`; keep spoken text separate from caption display punctuation when needed.
5. Generate `shotlist.csv` from the evidence arc.
6. Inspect media, render, probe/decode the final artifact, inspect representative frames, and run QA.

For venue-specific compliance, use `assets/conference_profile.template.json`. Verify hard constraints from current official conference, publisher, or journal pages/PDFs, with URL, date, locator, and source ID. Leave unknown values unset and label unverified requirements; do not fill them from memory.

## Deterministic tools

```text
python scripts/check_dependencies.py --json-out dependencies.json
python scripts/inspect_media.py INPUT... --recursive --output media_inventory.json --strict
python scripts/make_subtitles.py narration_timed.md --output subtitles.srt --strict
python scripts/render_video.py storyboard.json --subtitles subtitles.srt --output academic_video.mp4
python scripts/qa_video.py academic_video.mp4 --storyboard storyboard.json --inventory media_inventory.json --subtitles subtitles.srt --render-manifest render_manifest.json --visual-review visual_review.json --output-md qa_report.md --output-json qa_report.json
```

Add `--profile conference_profile.json` to render/QA when applicable. Use explicit executable paths if needed. Preserve commands, versions, source/output hashes, audio policy, and actual disclosures. The open-source CLI workflow must remain usable without a commercial editor.

Narration audio may be supplied or synthesized using an authorized available service. Do not claim the bundled scripts generate speech. Do not send unrelated sources to a remote service or certify pronunciation from text/timing checks alone.

## Integrity and QA

- Preserve observed events, data semantics, uncertainty, and relevant unfavorable evidence.
- Clearly distinguish observed, simulated, and explanatory media.
- Disclose changed playback, interpolation, stabilization, selected examples, and editorial repeats.
- Comparisons need explicit labels, aligned clocks, matched presentation conditions, and documented exceptions.
- Quantitative claims need metric, units, sample size, comparison, and aggregation where applicable.
- Never present generated media as an observed experiment.

`FAIL` means a P0 blocker and cannot be delivered as complete. `WARN` requires an explanation of remaining P1 findings. `PASS` requires automated checks and an actual visual review bound to the final hash. Automation does not certify scientific validity, intelligibility, or occlusion by itself.

## Publishable skill versus private project

Templates and public tests must be neutral and independently synthetic. Do not incorporate any current or past user's research case, identifiers, source content, data, or machine-specific paths, even by renaming or translating them.

Project materials and generated deliverables belong in private project directories, not this Skill. Do not commit credentials, local diagnostics, media, narration caches, or research outputs as examples.

## Delivery

Under `final/`, produce:

```text
academic_video.mp4
subtitles.srt
narration.md
storyboard.md
storyboard.json
shotlist.csv
evidence_map.md
media_inventory.json
qa_report.md
qa_report.json
sources.md
```

Also retain claim/timing records, applicable conference profile, render manifest, and visual review. If an artifact cannot be generated, state what is missing rather than substituting invented content.
