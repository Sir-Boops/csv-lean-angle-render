# CSV Lean Angle Renderer

Generate a transparent, frame-accurate PNG image sequence of a minimalist motorcycle lean-angle gauge from CSV data. Designed for **Apple Motion** and **Final Cut Pro**.

The display has:
- A clean upper **180° semicircular arc** (no arc numbers or tick marks except zero)
- A **moving dot** for the current signed lean angle
- A **fixed, short 0° marker** at the top of the arc
- One **positive-only number** below the arc (e.g., both -42 and +42 display `42°`)
- Transparent background, Skia native antialiasing, optional supersampling, multiprocessing, a Terminal progress bar and resumable rendering

**Signed angle convention:** negative = lean left; positive = lean right; zero = upright. Values outside -90° to +90° are clamped to the endpoints.

## Requirements

- macOS (recommended for the default font and Apple Motion workflow)
- Python 3.10+
- Skia (`skia-python`) and Pillow (see `requirements.txt`)

## Installation

Clone this repository and set up a Python virtual environment:

~~~bash
git clone https://github.com/Sir-Boops/csv-lean-angle-render.git
cd csv-lean-angle-render
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
~~~

If Python and the dependencies are already available, run the script directly.
Skia binary packages are available for Intel and Apple Silicon Macs; use a
Python version with a matching wheel.

## CSV format

The CSV must contain `lean_angle` **or** `value`. An optional `frame` column gives explicit **zero-based frame numbers**.

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

See [examples/sample_lean.csv](examples/sample_lean.csv).

You can also omit the `frame` column:

~~~csv
lean_angle
0
-10
-25
0
25
~~~

In that case, every row corresponds to the next frame, beginning at frame 0. If explicit frame numbers have gaps, the last known value is held for the missing frames; gaps before the first specified frame show 0°.

## Render frames

Use the included example:

~~~bash
python3 generate_lean_angle_resume.py examples/sample_lean.csv
~~~

The default output folder is `lean_frames/` next to your CSV file. To choose the destination:

~~~bash
python3 generate_lean_angle_resume.py examples/sample_lean.csv -o ./my_frames
~~~

For a 60-fps project with native antialiasing and four rendering workers:

~~~bash
python3 generate_lean_angle_resume.py examples/sample_lean.csv --fps 60 --workers 4 -o ./my_frames
~~~

### Command-line options

| Option | Default | Description |
| --- | --- | --- |
| `csv_path` | required | Path to the input CSV |
| `-o, --output` | `lean_frames` beside CSV | Folder for generated PNGs |
| `--fps` | `30` | **Intended playback** frames per second; positive, up to 240 |
| `--width` | `1920` | PNG width |
| `--height` | `1080` | PNG height |
| `--aa` | `1` for Skia; `4` for Pillow | Supersampling scale, 1 through 8; Skia AA=1 uses native antialiasing |
| `--backend` | `skia` | Graphics engine: `skia` or legacy `pillow` |
| `--workers` | logical CPU count + 1 | Concurrent worker processes |
| `--restart` | off | Delete renderer-owned frames and rerender from scratch |
| `--adopt-existing` | off | Reuse older frames without a manifest **only if** data and settings match |

**RAM caution:** 4× AA at 1920×1080 creates large intermediate images. Automatic workers may consume substantial memory. For many Macs `--workers 2` or `--workers 4` is a good first try. More processes do not always run faster.

### Skia rendering and migration from Pillow

Skia is the default CPU graphics engine. It draws the arc, dot, zero marker
and numeric label, and encodes the transparent PNGs. No GPU setup is required.
The default `--aa 1` uses Skia's native antialiasing at the output resolution,
avoiding the large 4× intermediate canvas. For optional supersampling, pass
`--aa 2` through `--aa 8`; Skia downsamples with Mitchell cubic filtering.
Pillow is retained for font selection/anchor metrics and completed-PNG
validation, plus the original graphics backend.

The minimalist design and data conventions are preserved. Skia and Pillow do
not produce identical pixels: text hinting, edge coverage, fractional rounding
and alpha blending may differ slightly. The initial Linux benchmark measured
roughly 8–10× faster end-to-end rendering with native Skia than Pillow AA=4;
macOS speed and Arial appearance still need local verification.
See [benchmark results and example frames](docs/benchmark/RESULTS.md).

**Existing Pillow renders:** use a new folder for Skia, or explicitly select
`--restart` to replace renderer-owned frames. Manifests prevent mixing backends.
Do not use `--adopt-existing` to adopt Pillow frames into a Skia render.
To resume an older Pillow sequence without changing its pixels, keep the same
CSV/settings/output folder and add `--backend pillow`:

~~~bash
python3 generate_lean_angle_resume.py data.csv --backend pillow -o ./existing_pillow_frames
~~~

Pillow keeps its original `--aa 4` default and LANCZOS downsampling; pass the
original `--aa` explicitly if it was different. Legacy Pillow manifests remain
compatible. Skia manifests record the backend, Skia version and resolved font
hash, as well as CSV and visual settings. Changing backend, font, Skia version
or AA requires a new folder or `--restart`. The font/version identity check
applies to Skia; legacy Pillow manifests retain their original format.
`requirements-skia.txt` remains an installation alias for the earlier prototype.

### Important: FPS and CSV frame numbers

PNGs **do not embed a frame rate**. The `--fps` option records intended playback FPS in `sequence_info.txt` and displays the calculated animation duration. It **does not** resample data or create extra frames. Set the **source image-sequence FPS** in Apple Motion to match.

Example: 300 PNGs play for 10 seconds at 30 fps or 5 seconds at 60 fps. If you change FPS, ensure the CSV's frame numbers represent the timing you intend.

## Stop and resume

While rendering, press **Ctrl+C once** in Terminal. In-flight frames are allowed to finish, and existing completed PNGs are preserved.

To resume, rerun **the same command with the same CSV, settings and output folder**:

~~~bash
python3 generate_lean_angle_resume.py examples/sample_lean.csv --fps 60 -o ./my_frames
~~~

The script scans output files and skips valid existing PNGs. It atomically saves new PNGs (temporary file, then rename), re-renders missing or invalid frames, and shows progress based on completed frames.

A `render_manifest.json` prevents accidentally mixing a new CSV or altered visual settings with older frames. If you want to change the source CSV or rendering settings in an existing output folder, specify `--restart` or use a **new folder**. `--restart` deletes renderer-matching `lean_*.png` images in that folder. **Back up anything important in your output folder first.**

When migrating frames from a previous version that didn't write a manifest, `--adopt-existing` can reuse them, but only if you're certain the CSV and appearance settings are identical.

## Number-only speed overlay

Use the separate `generate_speed_resume.py` script to render **only a centered
white whole-number speed** on a transparent background. It uses the same font
and proportional font size as the lean gauge. No units, degree symbol, arc,
dot or other graphics are rendered. Values are displayed without conversion.

~~~bash
python3 generate_speed_resume.py examples/sample_speed.csv --fps 60 --workers 4
python3 generate_speed_resume.py your_speed.csv -o ./my_speed_frames
~~~

The CSV accepts `speed` or `value`, with optional zero-based `frame`:

~~~csv
frame,speed
0,0
1,25.4
2,72
3,120
~~~

Without `frame`, each row becomes one frame starting at zero. Frame gaps hold
the previous speed; frames before the first reading show 0. Speeds must be
finite and nonnegative and are **not clamped to 90**. The number uses Python's
`round()` (halfway values round to the nearest even integer, so 72.5 → 72 and
73.5 → 74), consistent with the lean renderer's numeric rounding.

Output defaults to `speed_frames/` beside the CSV, with transparent RGBA PNGs
named `speed_000000.png`, etc. Defaults are 1920×1080, native Skia AA=1,
30 FPS and logical CPU count + 1 workers. Supported options are `-o/--output`,
`--width`, `--height`, `--aa` (1–8), `--fps`, `--workers`, `--restart` and
`--adopt-existing`; run `--help` for details. The speed script uses Skia only.

Ctrl+C safely stops rendering. Rerun the same command to automatically skip
valid frames and regenerate missing or damaged files. The manifest records
speed renderer identity, source CSV, visual settings, Skia version and font
hash. Use separate folders for speed and lean sequences; their manifests are
incompatible. `--restart` deletes only numbered `speed_*.png` frames, replaces
the manifest and sequence info, and preserves unrelated files. Adopt existing
frames only when you know they came from this speed script with matching data
and settings. FPS records playback metadata only; set the same source sequence
FPS when importing `speed_000000.png` into Motion.

## Import into Apple Motion

1. Open a Motion project at the desired resolution and intended FPS.
2. Choose **File → Import → Media**.
3. Navigate to your output folder and select `lean_000000.png`.
4. Enable **Image Sequence** in the import dialog (expand **Options** if necessary).
5. Import it as **one animated image-sequence layer**.
6. In the Media inspector, check/set the source frame rate to the value used for `--fps`.
7. Position the layer over your footage or graphics. The PNG background is transparent.

To reuse it in Final Cut Pro, you can convert the Motion project to a Final Cut Pro Generator (`File → Convert Project To → Generator`) and publish it. Another option is exporting an alpha-capable `ProRes 4444` video from Motion, then importing the `.mov` into Final Cut Pro.

## Output files

Example at ten CSV frames:

~~~text
my_frames/
  lean_000000.png
  lean_000001.png
  ...
  lean_000009.png
  render_manifest.json
  sequence_info.txt
~~~

The output PNGs are RGBA (transparent). The script does not produce video directly.

## Customization

Adjust the constants near the top of `generate_lean_angle_resume.py`: `ARC_COLOR`, `DOT_COLOR`, `TEXT_COLOR`, `ARC_WIDTH`, `DOT_RADIUS`, `ZERO_TICK_LENGTH`, `ZERO_TICK_WIDTH`, `FONT_SIZE` and `FONT_PATH`.

When changing visuals for an existing render, use a fresh output folder or `--restart` to avoid mixing different styles.

## Development

With the dependencies installed, run the backend integration checks:

~~~bash
python3 -m unittest discover -s tests -v
python3 tools/benchmark_renderers.py --output ./benchmark-local
~~~

The benchmark renders 24 frames at 1920×1080 into fresh folders, three times
per configuration, with one and four workers. It compares Pillow AA=4, Skia
AA=1, and Skia AA=4; these are different rendering/antialiasing configurations,
not pixel-equivalent quality settings. It includes process startup and PNG
encoding/writes, and saves raw timings plus a median summary in `results.json`.
Choose a new output path each time. Override `--frames`, `--repeats` or
`--workers` for longer runs. The benchmark produces numbered PNGs and can use
significant disk space. Timing is wall-clock, without an explicit filesystem
sync; it measures normal CLI completion rather than durable disk throughput.

See [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) for the full practical project handoff and [AGENTS.md](AGENTS.md) for guidance to coding agents such as Codex.

This is a Python/Skia image sequence generator, not a Motion plug-in. The rendered graphics can be composited in Motion or Final Cut Pro.
