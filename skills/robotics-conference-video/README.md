# Academic Conference Video

A field-independent Skill for supplementary videos, conference presentations, and academic paper teasers. Its installation identifier is `robotics-conference-video`; the workflow is not tied to a research subject.

## Runtime

Python 3.10+, FFmpeg/ffprobe, and a suitable installed font. Pillow is optional for annotation layers or contact sheets. No fonts or source material are bundled.

```text
python scripts/inspect_media.py source_media --recursive --output final/media_inventory.json --strict
python scripts/make_subtitles.py final/narration_timed.md --output final/subtitles.srt --strict
python scripts/render_video.py final/storyboard.json --subtitles final/subtitles.srt --output final/academic_video.mp4
python scripts/qa_video.py final/academic_video.mp4 --storyboard final/storyboard.json --inventory final/media_inventory.json --subtitles final/subtitles.srt --render-manifest final/render_manifest.json --visual-review final/visual_review.json --output-md final/qa_report.md --output-json final/qa_report.json
```

For venue compliance, add `--profile final/conference_profile.json` to render and QA after verifying its current official sources. Omitting a profile does not establish submission compliance. Scripts accept explicit `--ffmpeg` / `--ffprobe` paths.

## Contract

A storyboard must state one of the three academic modes, sources, source ranges, claims and locators, visual layout, narration, captions, transitions, playback factors, and processing disclosures. The renderer supports concatenation, trim, crop, scale, padding, comparisons, picture-in-picture, static explanatory cards, overlays, text, narration/source-audio policy, fades, and subtitle burning.

Evidence mapping and scientific interpretation remain source-grounded reasoning tasks. The bundled scripts do not invent claims, transcribe a paper, synthesize speech, or certify pronunciation.

## Tests

```text
python scripts/test_academic_video.py --unit-only
python scripts/test_academic_video.py --artifacts-dir <new-local-directory>
```

Only independently generated, abstract synthetic fixtures are used. Inspect the retained frames before creating a visual-review record. Documentation and production outputs stay separate.
