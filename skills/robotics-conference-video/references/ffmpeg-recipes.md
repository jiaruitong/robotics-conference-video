# Reproducible FFmpeg rendering

Use bundled scripts first. Keep source files unchanged and retain commands, hashes, versions, and processing disclosures.

## Model

Render segments independently; normalize resolution, frame rate, sample aspect ratio, pixel format, and audio. In a final encoding pass, trim each decoded video/audio stream to its planned segment interval, reset PTS, and filter-concatenate the standardized intermediates. Explicitly normalize the final frame clock and duration; optionally burn captions in this same pass. This prevents audio packet padding from shifting segment boundaries or adding a final video frame. Save the actual commands in render_manifest.json. Default to software H.264/AAC; hardware encoding is an explicit choice.

## Filter mapping

| Operation | Mechanism |
|---|---|
| Trim | input -ss/-t, trim/atrim, PTS reset |
| Crop | crop before fitting |
| Scale/padding | scale with preserved aspect ratio, pad |
| Frame rate | fps, output -r |
| Changed playback | setpts and chained atempo |
| Disclosed freeze | tpad clone |
| Comparison/PiP | stack or overlay |
| Labels/boxes | drawtext, drawbox |
| Figures/geometric layers | authorized image input, overlay |
| Narration/audio policy | normalized sample rate/channels, volume, mixing, limiter |
| Transitions | fade/afade |
| Captions | external SRT; subtitles/libass for burning |

A comparison applies the same playback factor unless a scientifically meaningful exception is documented. Ordinary frame-rate conversion is not motion interpolation; actual interpolation must be disclosed.

## Data contract

Layouts: single, side_by_side/split_horizontal, top_bottom/split_vertical, picture_in_picture/pip. Source references can include in_time, out_time, crop, label, and freeze_last. Editorial layers use visual.boxes and visual.overlays; disclosures use overlay_text.

For static images, avoid unnecessarily decoding at every output frame while preserving the full video clock. Repeat overlay inputs through the last frame. Verify frame count and persistence with synthetic test patterns.

## Audio and speech

Use narration.audio_asset_id for supplied speech audio. Explicitly state source-audio use, mixing, and volumes; do not silently replace requested audio. Silent normalization is a rendering mechanism, not permission to omit planned narration.

The CLI does not synthesize speech or certify pronunciation. Keep speech-provider settings and actual timing in the private project. Validate final audio for silence and clipping.

## Captions

Generate SRT from strict timed Markdown. Burn only when requested/required and when libass and the needed glyphs are available. Fonts are system resources, never bundled.

Subtitle size is expressed in output pixels and converted to libass script units. Set style.subtitle_font_size, subtitle_margin_fraction, and optionally subtitle_font_name. A drawtext font file does not by itself configure the installed family used by burned captions.

## Portability and safety

Use argv with shell=False, explicit executable paths when needed, and pathlib. Expect Unicode and spaces. Pass concat inputs as separate argv paths rather than shell-built commands. Review rotation, VFR, unusual timestamps, aspect ratio, bit depth/HDR, and audio tracks before editing.

Do not overwrite final output without explicit --overwrite. Keep a staged output until assembly succeeds. Commercial editor guidance may supplement the storyboard but does not replace reproducible FFmpeg rendering or final-file QA.
