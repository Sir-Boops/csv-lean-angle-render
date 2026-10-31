# Instructions for Coding Agents (Codex, etc.)

## Project goal
Maintain a command-line Python renderer that converts signed lean-angle CSV readings into transparent RGBA PNG frames for Apple Motion and Final Cut Pro. The visual style is intentionally **minimal**.

## Canonical entry point
`generate_lean_angle_resume.py` is the latest and primary script. Do not replace it with an earlier version from the history. Read `README.md` and `PROJECT_CONTEXT.md` first.

## Behavior to preserve
- Read CSV with `lean_angle` or `value` header, optionally `frame` (zero-based).
- Default one row per frame when `frame` is absent; gaps hold the last angle (0 before first).
- Negative = left, positive = right; display `abs(round(angle))` with degree symbol, and clamp lean to [-90, 90].
- One clean top semicircular arc, moving dot on arc, short fixed tick at the apex (0°), large number below. **Do not add arc labels, extra ticks, needles, or backgrounds** unless requested.
- RGBA transparent PNG output named `lean_000000.png`, etc.
- Skia is the default CPU graphics backend with native antialiasing (`--aa 1`). Optional supersampling (`--aa` 2–8) uses Mitchell cubic downsampling. Keep `--backend pillow` for legacy resumes/comparison, defaulting to AA=4 and LANCZOS.
- Configurable width/height, intended playback FPS (`--fps`, default 30), output folder, and multiprocessing worker count (logical CPU cores + 1 by default).
- Terminal progress bar, safe Ctrl+C interruption, resumable rendering, atomic frame writes, and manifest validation.
- `--restart` and `--adopt-existing` semantics.

## Safety / correctness rules
1. **PNG does not encode playback FPS.** Do not imply `--fps` re-times or resamples the CSV.
2. If CSV data or visual settings change, never silently reuse old frames. Honor `render_manifest.json`.
3. A completed `lean_*.png` should only appear after a successful atomic save. Re-render invalid PNGs.
4. Keep the `if __name__ == "__main__":` guard for Python multiprocessing on macOS.
5. Take memory usage seriously: 4x AA at 1080p creates large temporary canvases. Do not introduce unbounded task queues or large accumulations of image objects.
6. Treat Ctrl+C as a safe stop, not a deletion request. Preserve successfully completed PNGs.
7. Changes to the visual renderer should be tested against reference frames when feasible. Avoid unintended visual changes in optimization-only patches.
8. Keep README and `PROJECT_CONTEXT.md` synchronized with feature and CLI changes.
9. Preserve backend/version/font manifest identity for Skia, and legacy Pillow manifest compatibility. Never mix frames from different backends.
10. Skia drawing/encoding lives in `skia_backend.py`; the canonical entry point owns scheduling, CSV, and safe saves. Pillow is retained for font metrics and PNG validation. Run `python3 -m unittest discover -s tests -v` after backend changes.

## Suggested checks
~~~bash
python3 -m pip install -r requirements.txt
python3 generate_lean_angle_resume.py --help
python3 generate_lean_angle_resume.py examples/sample_lean.csv --workers 1 --aa 1 --width 320 --height 180 -o /tmp/lean-angle-check
python3 generate_lean_angle_resume.py examples/sample_lean.csv --workers 1 --aa 1 --width 320 --height 180 -o /tmp/lean-angle-check
~~~
The second run should reuse completed PNGs. Check output PNG dimensions and transparency. Test Ctrl+C and resume with a longer CSV when changing scheduling or concurrency.

## Collaboration
Explain any behavior changes, document new flags, and prefer small verifiable updates. If instructions in a task conflict with existing behavior, mention the tradeoff and implement the user's explicit request.

## Separate speed renderer

`generate_speed_resume.py` renders only a centered white whole-number speed
on transparency. Preserve the absence of units, degree symbols and gauge
artwork. Accept `speed`/`value` with optional `frame`; hold gaps, reject
negative/nonfinite speeds and never apply the lean-angle clamp. Keep speed
frames (`speed_*.png`) and manifest identity separate from lean output.
Update both user docs when changing speed behavior and run the full unittest
suite. The standalone speed CLI uses Skia only and preserves the same safe
resume, bounded scheduling, FPS metadata and macOS multiprocessing semantics.
