# Academic visual style

Make evidence readable with minimal text, consistent type, restrained transitions, and clear visual hierarchy.

## Canvas and text

Use verified requirements when applicable. Working defaults: 16:9, 1920×1080, square pixels, constant 30 fps. Keep critical content inside a 5% safe area and reserve explicit caption/disclosure regions.

Use an installed sans-serif font with glyph coverage for the requested language. Do not bundle fonts. At 1080p, start around 52–72 px for titles, 42–52 px for headings, and 36–44 px for captions/labels; adjust by frame height and inspect actual output.

Use high contrast and semantic line breaks. Avoid shrinking text to accommodate unnecessary prose.

## Consistent comparison semantics

Use stable condition colors and labels. A color-accessible default is blue #0072B2, orange #E69F00, and neutral blue-green #009E73. Never use red/green as the only distinction. Match view size, time scale, plot scale, and emphasis where comparison requires fairness.

## Figures and data

Preserve axes, units, legends, scale bars, uncertainty, panel identity, and data semantics. Focus individual panels when needed. Redraw only from authorized underlying values and record the transformation. Maintain source locators in the evidence package even when no source label appears on screen.

Editorial highlights draw attention; they do not create measurements.

## Layouts

Single view, side-by-side, top/bottom, picture-in-picture, and figure overlays should be chosen for comprehension, not decorative variety. Declare protected regions around evidence-bearing content. Captions and overlays must not obscure essential labels, plot axes, legends, or observations.

The renderer automatically places source comparison labels at normalized (x, y) positions (0.04, 0.05) and (0.54, 0.05) for side-by-side views, or (0.04, 0.05) and (0.04, 0.55) for top/bottom views. Reserve these regions when positioning titles or disclosures, accounting for the actual text width and height. Automated checks do not establish that these rendered labels are collision-free; inspect the composed frame.

## Motion and captions

Use simple cuts or short fades. Disclose explanatory repeats, freezes, speed changes, interpolation, and stabilization. Do not introduce effects that change scientific interpretation.

Captions use one or two readable semantic lines with a high-contrast backing. Inspect actual glyphs, placement, clipping, and timing. Use external SRT; burn captions when requested or required.

## Closing

Summarize the supported conclusion and material limitation. Include only requested, authorized, policy-compliant identification. Follow current anonymization and conference branding rules.
