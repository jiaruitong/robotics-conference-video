# Conference requirements and provenance

Requirements vary by venue, year, track, and video type. This file intentionally provides no remembered submission limits.

## Official authority

Use the current official conference page/PDF or its linked official publisher/journal instructions. A manuscript-specific submission-system or editor instruction may take priority when documented. Third-party summaries and earlier years cannot establish current hard constraints.

Confirm applicability to the requested track and academic video type. Record URL, title, precise section/page, checked date, source ID, and whether each value is mandatory, recommended, or unstated. Do not convert a recommendation into a blocker.

If official sources disagree, record the conflict and seek clarification instead of claiming compliance.

## Profile

Use assets/conference_profile.template.json. Include schema_version, venue, year, video_type, verification_status, checked_date, official_sources, constraints, and submission_notes.

Constraints can cover duration, file size, container, codec, resolution, fps, aspect ratio, audio, captions, anonymity, permitted branding, and citation policy. Store duration in seconds and file size in bytes. Use canonical codec names such as h264, not the encoder name libx264. Preserve exact/minimum/maximum/allowed-list distinctions.

Each asserted hard constraint needs source_id and requirement_level. Leave unstated values null/unknown.

## Unavailable sources

Set verification_status to unverified; leave unknown constraints unset; state that submission compliance is not established. Ask for the official page/PDF when needed. Production may continue with clearly documented working settings, but those settings are not official rules.

Verified means actual official sources were inspected and support the recorded hard constraints. Test means exclusively a synthetic validation fixture, never an official venue or compliance claim.

## QA

Measure the final artifact, not its filename or storyboard declaration. A verified mandatory violation is a P0 gate. An unverified profile raises a warning and cannot support a compliance assertion.

Keep source links and provenance in sources.md without reproducing unnecessary copyrighted text.
