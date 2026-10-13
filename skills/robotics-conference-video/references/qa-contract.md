# Final-artifact QA contract

A valid storyboard is not proof that rendered pixels, sound, and captions satisfy it.

## Gate and exits

PASS: no P0/P1 findings and actual hash-bound visual review passes.
WARN: no P0 but at least one warning or unperformed manual review.
FAIL: at least one P0 blocker.

Exit 0 means PASS/WARN, 1 means FAIL (or WARN with --fail-on-warn), and 2 means missing dependencies or invalid audit inputs. Never erase a finding to improve the status.

## P0 blockers

Unreadable media or decode error; missing/changed assets; invalid source ranges; timeline gaps/overlaps; output duration mismatch; unsupported mode; missing required narration; invalid subtitle timestamps; required audio absent or effectively silent; clipping; mandatory verified profile violations; a verified profile lacking sources; missing scientific disclosures; manifest/segment mismatch; known identifying content under required anonymity.

## P1 warnings

Unverified requirements, dense or small captions/text, unsafe placement, unclear comparisons, undocumented synchronization, incomplete quantitative context, fast narration, sustained black/silent/static intervals, or pending/stale visual review.

## Automated checks

Use ffprobe for actual streams/duration/resolution/fps/container/audio. Fully decode the output with error escalation. Use blackdetect, silencedetect, volumedetect, and freezedetect. Audit source/hash/timeline/manifest consistency, SRT timing/density, and applicable conference constraints. Do not rely on a filename or declared canvas.

## Manual checks

Inspect actual representative frames and every evidence-heavy layout. Review visibility, figure readability, label clarity, color accessibility, and anonymization. Save visual_review.json with schema_version, video_sha256, reviewed_at, reviewer, status, boolean checks (subject_visible, plot_readable, labels_clear, color_accessible, anonymization_checked), and notes.

The review becomes stale whenever the video's bytes change. Script-generated synthetic tests must not attest an unperformed review. Automated QA does not validate scientific truth or pronunciation.

## Regression gates

Independently generated fixtures should exercise:

- the three accepted academic modes and rejection of unsupported modes;
- invalid/overlapping timelines, missing/changed sources, invalid ranges;
- simulation, playback, processing, and selection disclosures;
- subtitle timestamp/density and required audio;
- verified source linkage, unknown requirements, and wrong output specifications;
- actual trim/concat/scale/pad/layout/overlay/subtitle/audio rendering;
- static-input frame clocks, final-frame persistence, and transparent overlays.

Use only abstract generated patterns, geometric layers, and tones under neutral names. Test data are not research measurements.
