# Project Handoff / Conversation Context

This is a **shareable project summary** for a future ChatGPT Work / Codex session. It captures the user-facing technical decisions from the development conversation; it is not a dump of ChatGPT's hidden internal instructions or reasoning.

## Original use case

User has CSV readings representing a motorcycle's lean angle and wants them synchronized to video frames in Apple Motion, then overlaid in Final Cut Pro. The approach selected is to render **one transparent PNG per animation frame**, then import the numbered files as an **image sequence** in Motion. FPS must be selected in Motion because a PNG sequence has no embedded frame-rate metadata.

## Evolution of the design

1. Initially generated a number per CSV frame on a transparent PNG.
2. Switched to signed motorcycle lean-angle visualization: 180-degree top semicircle with 0° at the apex; left lean negative, right lean positive.
3. User wanted minimalism: **no arc numbers, no other tick marks, no left/right labels, no needle**. Only one numeric reading below.
4. Dot moves along the semicircle: negative left, 0 at top, positive right.
5. Number below always shows absolute angle (e.g. -45 and +45 both show 45°).
6. Added high-quality anti-aliasing by rendering at 4× resolution and downsampling with LANCZOS. `--aa` accepts 1 to 8.
7. Added a single short **stationary 0° tick** at the apex.
8. Added Terminal progress display with percentage and count of rendered frames.
9. Added multiprocessing (separate processes, not threads) to accelerate independent image generation. `--workers` default is `os.cpu_count() + 1`, override manually for memory efficiency.
10. Added `--fps` for intended playback frame rate, while preserving CSV-to-frame mappings.
11. Added Ctrl+C safe stopping and later resumption, atomic PNG writes, validity checks, CSV/settings manifest, `--restart`, and `--adopt-existing`.
12. Replaced the default graphics pipeline with CPU Skia native antialiasing (`--aa 1`) for speed. Kept the original Pillow backend (`--backend pillow`, default AA=4) for legacy resumes and comparison.

## Canonical source

`generate_lean_angle_resume.py` is the most recent, feature-complete script and the canonical CLI to maintain. Prior experimental scripts (AA, progress, parallel, auto-workers, custom FPS) were superseded.

Graphics default: `skia_backend.py` draws and encodes PNGs. The primary script
retains CSV parsing, multiprocessing, manifests, atomic saves, validation,
progress and resume. Pillow supplies font selection/anchor metrics and PNG
validation, and its original drawing function is kept for legacy renders.

Other files:
- `README.md` — setup, examples, CLI reference, Motion / Final Cut import workflow
- `AGENTS.md` — coding agent development instructions
- `requirements.txt` — Skia and Pillow dependencies
- `requirements-skia.txt` — compatibility alias to `requirements.txt`
- `examples/sample_lean.csv` — example inputs

## Data conventions and frame timing

Typical input:

~~~csv
frame,lean_angle
0,0
1,-10
2,-25
3,-40
4,-55
5,-30
6,0
7,20
8,35
9,50
~~~

A `value` header also works instead of `lean_angle`. The `frame` column is optional and zero-based. If absent, row index is frame index. If frame numbers skip, their preceding angle holds. Clamp values to -90..90. Missing frames before first input use 0°.

The `--fps` flag does *not* resample the input. It only writes the intended playback rate and duration into `sequence_info.txt`. For example, 300 PNGs will last 10 seconds at 30fps or 5 seconds at 60fps. The source sequence FPS in Motion must be set accordingly.

## Visual design / coordinates

Canvas default: 1920×1080 RGBA transparent. Default rendering is now Skia
native antialiasing at AA=1. The coordinates below describe the shared design;
Skia insets the arc path by half its stroke width to match Pillow inside strokes. Center at x=50% width, y=52% height. Arc radius is min(34% of width, 28% of height). Arc is upper semicircle from left (180°) through top (270°) to right (360°) as interpreted by Pillow.

Move the dot according to `theta_degrees = 270 + lean_angle`, converted to radians for arc position. Dot and number are white. Arc is softer/partially transparent white. Short 0° tick crosses the apex. Large number is centered near 73% of frame height. The numeric text is `abs(round(lean_angle))` plus the degree symbol.

Current constants in the script (adjustable at top): `WIDTH`, `HEIGHT`, `FPS`, `AA`, `PILLOW_AA`, `ARC_COLOR`, `DOT_COLOR`, `TEXT_COLOR`, `ARC_WIDTH`, `DOT_RADIUS`, `ZERO_TICK_LENGTH`, `ZERO_TICK_WIDTH`, `FONT_SIZE`, `FONT_PATH`.

## Rendering pipeline

- Input parsed via `csv.DictReader`; CSV is hashed for manifest.
- Task generator maps every frame to the held signed angle.
- Spawn parallel processes with `ProcessPoolExecutor` (or run sequentially for one worker).
- Each worker reuses a CPU Skia surface, draws with native antialiasing, and encodes PNGs. Optional AA>1 uses Mitchell cubic downsampling. The legacy Pillow backend uses LANCZOS.
- Each output is saved first to a temporary file in the destination folder, then atomically renamed to `lean_NNNNNN.png`.
- Parent process tracks completed frames and updates Terminal progress.
- On start, valid existing PNGs are recognized and skipped; invalid ones are regenerated.
- `render_manifest.json` protects against mismatched CSV data or render settings; `sequence_info.txt` records intended FPS and duration.
- Ctrl+C allows in-flight work to finish; restart the same command to resume.

## Primary user commands

~~~bash
python3 -m pip install -r requirements.txt
python3 generate_lean_angle_resume.py examples/sample_lean.csv
python3 generate_lean_angle_resume.py examples/sample_lean.csv --fps 60 --workers 4 -o my_frames
# Stop with Ctrl+C. Resume with the same exact command.
~~~

When changing CSV or appearance, use a different output folder or add `--restart`. For old existing PNGs without a manifest, `--adopt-existing` is supported only when user knows they match.

## Apple Motion / Final Cut workflow

In Motion: create project at intended FPS → File > Import > Media → select first `lean_000000.png` → check Image Sequence → import as one layer → ensure source sequence frame rate matches `--fps`. Transparency allows composite over footage. For FCP: publish the Motion project as a Final Cut Pro Generator, or export transparent ProRes 4444 MOV and import that.

## Skia implementation and migration

Skia is the default backend, with native antialiasing (`--aa 1`). Install
`requirements.txt`; `requirements-skia.txt` is a compatibility alias. Skia runs
on CPU without GPU setup. Pillow remains for font selection/anchor metrics,
PNG validation and the legacy renderer. `--backend pillow` restores the
original drawing pipeline and defaults to AA=4 when `--aa` is omitted.
The shared CLI constant `AA` is now 1; `PILLOW_AA` is 4.

Skia AA>1 uses Mitchell cubic filtering, while Pillow uses LANCZOS. Designs
are visually comparable, not pixel-identical. No angles are quantized or cached.
The resolved font file is shared, and Pillow anchors position Skia's text.
Worker-local fonts, paints, surfaces and a bounded cache of numeric label
anchors are reused. Multiprocessing and the bounded task queue are unchanged.

Skia manifests include backend/version/font identity to prevent incompatible
reuse. Legacy Pillow manifest format is unchanged. Existing Pillow sequences
must use `--backend pillow` with their original settings to resume; new Skia
renders require a fresh folder or `--restart`. Never adopt a different
backend's frames with `--adopt-existing`.

`tools/benchmark_renderers.py` runs reproducible end-to-end comparisons;
`tests/test_backends.py` checks defaults, legacy compatibility, output, resume,
manifest safeguards, atomic saves, spawn workers, and interruption/resume.
See `docs/benchmark/RESULTS.md` and its example images for the initial prototype
comparison. These historical results used Linux and DejaVu Sans; macOS
performance and Arial rendering remain to be checked on a Mac. No macOS
benchmark is implied by Linux spawn tests.

## Known considerations / possible future work (not yet requested)

- Automatic worker count + 1 may consume too much RAM; user can set `--workers 2` or `--workers 4`.
- More workers may not improve throughput if memory pressure or disk I/O becomes the bottleneck.
- Use regression tests when changing visual appearance; maintain transparent alpha and pixel dimensions.
- Potential features to discuss rather than silently implement: decimal precision, different fonts, customizable graphics placements, estimated completion time, resampling timestamp-based CSV, movie export, packaged CLI.

## Expectations for future coding sessions

Use the GitHub repository as the durable source of truth. Preserve the minimalist gauge, existing CLI switches, resume guarantees and Motion workflow. Implement requested changes with tests and documentation. Avoid claiming PNGs embed an FPS.

## Number-only speed renderer

`generate_speed_resume.py` is a separate standalone Skia CLI. It draws only a
white whole-number speed, centered horizontally and vertically on a transparent
RGBA canvas, using the lean renderer's default font and proportional font size.
No units or degree symbol are rendered and no unit conversion is performed.
It accepts `speed` or `value`, optional zero-based `frame`, holds gaps (0 before
the first reading), rejects negative/nonfinite values, and does not clamp speeds
to the lean gauge's range. Python round-to-even is used for whole numbers.

Default output: `speed_frames/` beside CSV, `speed_000000.png`, etc. Supports
resolution, AA=1–8, FPS metadata, worker count, bounded multiprocessing, progress,
Ctrl+C, automatic resume, atomic saves, validation, `--restart` and
`--adopt-existing`. The manifest includes `renderer: speed-number`, source,
font and Skia identity. Keep speed and lean output folders separate; manifests
must never be mixed. Restart removes only numbered speed PNGs and replaces
manifest/sequence metadata. The original lean renderer is unchanged.

`examples/sample_speed.csv` and `tests/test_speed.py` cover the speed workflow,
including no-unit centered artwork, values above 90, mapping, corrupt/missing
frame recovery, manifest safety, atomic failures, spawn workers and Ctrl+C resume.
