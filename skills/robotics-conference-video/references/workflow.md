# Academic video workflow

Keep sources, interpretations, editing decisions, and rendering facts separate. Use a project directory containing source/, working/, and final/. Do not modify inputs in place or store private project outputs in the published Skill.

## Intake

Inspect supplied sources, then resolve only consequential missing facts: mode, venue/year/type, official requirements, duration, language, narration, captions, resolution, and anonymization. Record working choices separately from submission rules.

## A — Paper understanding

Read metadata, abstract, structure, figures, and results, then the method/evaluation passages needed to validate the video. Write paper_claims.md with IDs, precise statements, paper locators, source type, conditions, quantitative context, and limitations.

Separate direct source facts from user statements and production inferences. Missing evidence is a reason to qualify or omit a claim, not to invent a demonstration.

## B — Evidence map

Map each claim to an authorized figure, table, data item, or media range. Record asset ID, locator or source in/out time, role, transformations, caveat, and selection decision. One segment should answer one clear question. Repeated material needs a distinct evidential purpose.

Record whether material is observed, simulated, or explanatory, and any selection or processing that changes its interpretation. A filename does not establish provenance.

## C — Storyboard

Start from the empty JSON template. Each segment states ID, start/end/duration, role, claim ID, layout, assets and exact ranges, overlays, narration, transitions, playback factor, citations, and notes. Timeline starts at zero without gaps or overlaps. Changes to JSON must propagate to Markdown/CSV views.

An explanatory card may be generated, but must not assert an unobserved finding.

## D — Narration and captions

Write narration.md in the requested language; optional language-specific copies are useful only when requested. Use concrete, source-grounded speech, not a reading of paper paragraphs or equations.

Obtain speech audio before locking timing. Keep recording/TTS provenance, settings, hashes, and actual timing. Speech synthesis is optional and outside the bundled renderer. Only authorized narration text should be sent to a remote service.

Timed Markdown uses:

```md
## S001 [00:00:00.000 --> 00:00:04.000]
Academic Video Example uses independently generated synthetic material.
```

Caption display text may have different punctuation from spoken text. Split at semantic boundaries and align to actual audio; do not force a sentence-ending mark on every screen change. Mark approximate alignment. Inspect pronunciation when necessary; text validation is not an audio review.

## E — Editing plan

Generate shotlist.csv from the actual evidence arc, recording segment/time/claim/visual/source/overlay/narration/transition/playback/citation/notes. Confirm ranges have enough material after any declared playback change. Do not force a fixed timing template.

Title and conclusion holds follow the measured speech and comprehension needs. Document intentionally silent explanatory intervals rather than silently dropping planned narration.

## F — Rendering and QA

Inspect inputs with ffprobe; generate SRT; render with FFmpeg; retain exact commands, tool versions, and hashes. Probe and fully decode the final video. Review representative frames for readability, labels, source context, color accessibility, and anonymity.

Save visual_review.json with the actual final hash. Run qa_video.py on the final artifact, with applicable profile, inventory, captions, and manifest. Fix P0 findings, then render and recheck. Explain P1 findings; never call FAIL complete.

## Package

Deliver academic_video.mp4, subtitles.srt, narration.md, storyboard.md/json, shotlist.csv, evidence_map.md, media_inventory.json, sources.md, and qa_report.md/json. Retain paper_claims.md, narration_timed.md, render_manifest.json, visual_review.json, and conference_profile.json when applicable.

Public examples must be independent synthetic fixtures, not renamed versions of user work. Keep project data and local provenance private.
