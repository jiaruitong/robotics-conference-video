# Academic Conference Video

An evidence-grounded, field-independent academic video Skill. Only three modes are supported: paper supplementary video, conference presentation, and academic paper teaser.

The repository and installation identifier remain `robotics-conference-video` for compatibility. This identifier does not select a research field or provide subject-specific cases.

## Install

Use this repository's URL as `<repository-url>`:

```sh
npx skills add <repository-url> --skill robotics-conference-video --agent codex --global --copy --yes
```

Alternatively, with Python 3.10+:

```sh
git clone <repository-url> academic-video-skill
cd academic-video-skill
python tools/install_codex.py
python tools/install_codex.py --check
```

The installer respects `CODEX_HOME`, otherwise checks an existing `~/.agents/skills`, then `~/.codex/skills`. It copies the complete maintained directory. It refuses to overwrite an existing installation without `--update`; updating preserves an exact, timestamped local backup and leaves other skills unchanged.

```sh
python tools/install_codex.py --update
python tools/install_codex.py --check
```

Use the updated skill in a new Codex turn or session.

## Scope

| Mode | Purpose |
|---|---|
| `supplementary` | Make the paper's evidence and evaluation conditions inspectable |
| `presentation` | Explain the research question, method, evidence, and limitations |
| `teaser` | Present the central supported finding concisely, without omitting material caveats |

Input may include a paper, authorized figures/tables/data, recorded research media, a narration recording, and an official conference requirement URL or PDF. Duration, language, resolution, captions, and anonymization follow the current request and verified venue rules, not a previous project.

## Workflow

Paper claims → evidence map → storyboard → narration and timed captions → FFmpeg render → final-artifact QA.

Keep source-linked claims, exact source ranges, rendering commands, hashes, and human-readable views. A generated schematic is an explanation, not evidence of an unobserved result.

Required deliverables include `academic_video.mp4`, `subtitles.srt`, `narration.md`, `storyboard.md` / `.json`, `shotlist.csv`, `evidence_map.md`, `media_inventory.json`, `sources.md`, and `qa_report.md` / `.json`. Retain narration timing, conference profile, render manifest, and hash-bound visual review when applicable.

## Dependencies and use

Python 3.10+, FFmpeg/ffprobe with H.264, AAC, and subtitle support, and an appropriate installed font. No fonts, source media, user data, credentials, or speech-service account are distributed.

```sh
python skills/robotics-conference-video/scripts/check_dependencies.py --json-out <local-output>/dependencies.json
```

Example requests are neutral and do not encode a real research case:

```text
Use robotics-conference-video to create a supplementary video from the supplied paper and authorized evidence. Verify the current official submission requirements.
Use robotics-conference-video to create a conference presentation organized around the research question, method, results, and limitations.
Use robotics-conference-video to create an academic paper teaser with source-linked claims and reproducible rendering.
```

## Validation

```sh
python -m unittest discover -s tests -v
python skills/robotics-conference-video/scripts/test_academic_video.py
```

Tests independently generate abstract test patterns, geometric overlays, and audio tones. They use names such as Academic Video Example and Synthetic Test A. Test tones exercise audio plumbing, not speech pronunciation. Synthetic fixtures never represent a real paper, measured result, or experiment.

Use `--unit-only` for the deterministic non-rendering regressions. Use `--artifacts-dir <new-local-directory>` to retain a 24-second synthetic render, manifest, subtitles, and QA for inspection. Test code does not attest an unperformed visual review. CI tests Windows/Linux and Python 3.10/3.12 without publishing the generated media.

## Privacy and provenance

Public examples and tests must be independently synthetic. Do not derive them from current or past user projects, even under different names. Keep project sources, data, narration, manifests, and machine-specific diagnostics in separate local output directories; do not commit them as skill resources. Sending text to an optional speech service requires authorization and should not include unrelated material.

## License

MIT; see [LICENSE](LICENSE). Third-party tools, system fonts, services, and user sources retain their own licenses.
