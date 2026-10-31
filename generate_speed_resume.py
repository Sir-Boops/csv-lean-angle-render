#!/usr/bin/env python3
"""Render centered white speed numbers to transparent PNGs with CPU Skia.

Accepts speed or value CSV columns, optionally frame. No units or conversion.
Rerun the same command to resume valid frames; --restart explicitly replaces
speed frames. FPS records playback metadata only, without resampling.
"""

import argparse
import csv
import hashlib
import json
import math
import multiprocessing
import os
import re
import signal
import sys
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

from PIL import Image, ImageFont

WIDTH = 1920
HEIGHT = 1080
FPS = 30
AA = 1

TEXT_COLOR = (255, 255, 255, 255)
FONT_SIZE = 170
FONT_PATH = "/System/Library/Fonts/Supplemental/Arial.ttf"

# Each process loads its font once, instead of reloading it for every PNG.
_WORKER_OUTPUT = None
_WORKER_SKIA = None


def load_font(size):
    for font_path in (
        FONT_PATH,
        "/System/Library/Fonts/Supplemental/Helvetica.ttc",
        "DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(font_path, size)
        except OSError:
            pass
    raise RuntimeError("No TrueType font available; set FONT_PATH to an installed .ttf font")


def read_csv_rows(csv_file):
    with csv_file.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV must have a header row")
        rows = [{k.strip(): v for k, v in row.items() if k is not None} for row in reader]
        headers = {name.strip() for name in reader.fieldnames}

    field = "speed" if "speed" in headers else "value" if "value" in headers else None
    if field is None:
        raise ValueError("CSV must contain a 'speed' or 'value' column")
    if not rows:
        raise ValueError("CSV has no data rows")

    has_frame = "frame" in headers
    values = {}
    for index, row in enumerate(rows):
        frame = int(row["frame"]) if has_frame else index
        if frame < 0 or frame in values:
            raise ValueError(f"Invalid or duplicate frame number: {frame}")
        speed = float(row[field])
        if not math.isfinite(speed):
            raise ValueError(f"Non-finite speed at frame {frame}")
        if speed < 0:
            raise ValueError(f"Negative speed at frame {frame}")
        values[frame] = speed
    return values


class SpeedRenderer:
    """Reuse a transparent surface; draw only a visually centered numeric label."""

    def __init__(self, width, height, aa, metrics_font):
        import skia
        self.skia = skia
        self.width, self.height, self.aa = width, height, aa
        self.surface = skia.Surface(width * aa, height * aa)
        self.output = skia.Surface(width, height) if aa > 1 else None
        if self.surface is None or (aa > 1 and self.output is None):
            raise RuntimeError("Could not allocate Skia rendering surface")
        typeface = skia.Typeface.MakeFromFile(str(metrics_font.path))
        if typeface is None:
            raise RuntimeError(f"Skia could not load font: {metrics_font.path}")
        self.font = skia.Font(typeface, metrics_font.size)
        self.font.setEdging(skia.Font.Edging.kAntiAlias)
        self.font.setSubpixel(True)
        self.paint = skia.Paint(Color=skia.ColorSetARGB(TEXT_COLOR[3], *TEXT_COLOR[:3]),
                                AntiAlias=True)

    def save(self, output_file, speed):
        skia = self.skia
        canvas = self.surface.getCanvas()
        canvas.clear(skia.ColorTRANSPARENT)
        label = str(round(speed))
        bounds = skia.Rect()
        self.font.measureText(label, bounds=bounds)
        x = self.width * self.aa / 2 - (bounds.left() + bounds.right()) / 2
        y = self.height * self.aa / 2 - (bounds.top() + bounds.bottom()) / 2
        canvas.drawString(label, x, y, self.font, self.paint)
        image = self.surface.makeImageSnapshot()
        if self.output is not None:
            out = self.output.getCanvas()
            out.clear(skia.ColorTRANSPARENT)
            out.drawImageRect(image, skia.Rect.MakeWH(self.width, self.height),
                              skia.SamplingOptions(skia.CubicResampler.Mitchell()))
            image = self.output.makeImageSnapshot()
        data = image.encodeToData(skia.kPNG, 100)
        if data is None:
            raise RuntimeError("Skia PNG encoding failed")
        with open(output_file, "wb") as destination:
            destination.write(bytes(data))


def show_progress(completed, total):
    bar_width = 30
    fraction = completed / total
    filled = round(bar_width * fraction)
    bar = "#" * filled + "-" * (bar_width - filled)
    sys.stdout.write(
        f"\rRendering: [{bar}] {fraction:6.1%} "
        f"({completed:,}/{total:,} frames)"
    )
    sys.stdout.flush()


def frame_tasks(values):
    """Supply every frame in order, holding values for missing frame numbers."""
    current_speed = 0.0
    for frame in range(max(values) + 1):
        if frame in values:
            current_speed = values[frame]
        yield frame, current_speed


def init_worker(output_dir, width, height, aa):
    """Create worker-local font and surface; parent handles Ctrl+C."""
    if multiprocessing.current_process().name != "MainProcess":
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    global _WORKER_OUTPUT, _WORKER_SKIA
    _WORKER_OUTPUT = output_dir
    size = max(1, round(FONT_SIZE * aa * min(width / WIDTH, height / HEIGHT)))
    _WORKER_SKIA = SpeedRenderer(width, height, aa, load_font(size))


def render_task(task):
    """Render into a temp file; publish only fully saved PNGs."""
    frame, speed = task
    final_path = _WORKER_OUTPUT / f"speed_{frame:06d}.png"
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{final_path.name}.", suffix=".tmp", dir=_WORKER_OUTPUT
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        _WORKER_SKIA.save(temp_path, speed)
        os.replace(temp_path, final_path)  # Atomic within the same folder.
    finally:
        temp_path.unlink(missing_ok=True)
    return frame


def valid_frame_png(path, width, height):
    """Verify PNG data and dimensions before skipping a previous render."""
    if not path.is_file():
        return False
    try:
        with Image.open(path) as image:
            if (image.format != "PNG" or image.mode != "RGBA"
                    or image.size != (width, height)):
                return False
            image.verify()  # Checks PNG chunks / checksums.
        return True
    except (OSError, ValueError, SyntaxError):
        return False


def manifest_settings(csv_file, width, height, aa):
    """Identify source, renderer, font and settings before reusing any frames."""
    import skia
    digest = hashlib.sha256()
    with csv_file.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    size = max(1, round(FONT_SIZE * aa * min(width / WIDTH, height / HEIGHT)))
    font = load_font(size)
    return {
        "renderer": "speed-number",
        "render_version": 1,
        "csv_sha256": digest.hexdigest(),
        "width": width, "height": height, "aa": aa,
        "font_path": FONT_PATH, "font_size": FONT_SIZE,
        "text_color": list(TEXT_COLOR),
        "backend": "skia", "backend_version": skia.__version__,
        "resolved_font_sha256": hashlib.sha256(Path(font.path).read_bytes()).hexdigest(),
    }


def atomic_write_text(path, contents):
    """Avoid half-written manifest/info files after a process interruption."""
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(contents)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def cleanup_temp_frames(output_dir):
    """Remove leftovers from aborted saves, leaving other files untouched."""
    for file in output_dir.glob(".speed_*.png.*.tmp"):
        if re.fullmatch(r"\.speed_\d{6,}\.png\.[^.]+\.tmp", file.name):
            file.unlink(missing_ok=True)


def prepare_output(output_dir, settings, restart, adopt_existing):
    """Only reuse frames when their source and render settings are known."""
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "render_manifest.json"
    existing_manifest = manifest_path.exists()
    old_frames = list(output_dir.glob("speed_*.png")) if restart or not existing_manifest else []

    if restart:
        # Explicit opt-in: remove only frame files created by this renderer.
        for path in old_frames:
            if re.fullmatch(r"speed_\d{6,}\.png", path.name):
                path.unlink()
        manifest_path.unlink(missing_ok=True)
        (output_dir / "sequence_info.txt").unlink(missing_ok=True)
    elif existing_manifest:
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(
                "Existing render_manifest.json cannot be read. Use --restart "
                "to regenerate the sequence."
            ) from exc
        if previous != settings:
            raise ValueError(
                "Existing PNGs were generated with different CSV content or "
                "render settings. Use --restart to regenerate safely, or "
                "choose another output folder."
            )
    elif old_frames and not adopt_existing:
        raise ValueError(
            "Existing speed_*.png images have no render manifest (possibly "
            "from an older script). To reuse them, run with --adopt-existing "
            "ONLY if the CSV and visual settings are identical. Otherwise "
            "use --restart or a new output folder."
        )

    cleanup_temp_frames(output_dir)
    atomic_write_text(manifest_path, json.dumps(settings, indent=2) + "\n")


def write_sequence_info(output_dir, total_frames, width, height, fps):
    sequence_info = output_dir / "sequence_info.txt"
    atomic_write_text(
        sequence_info,
        f"Speed image sequence\n"
        f"Intended playback FPS: {fps:g}\n"
        f"Frames: {total_frames}\n"
        f"Duration: {total_frames / fps:.6f} seconds\n"
        f"First frame: speed_000000.png\n"
        f"Last frame: speed_{total_frames - 1:06d}.png\n"
        f"Resolution: {width}x{height}\n"
        "\n"
        "PNG images do not store an FPS. In Apple Motion, import as an Image "
        "Sequence and set its source frame rate to the intended FPS above.\n"
        "The CSV 'frame' column represents frame indices, not seconds.\n",
    )
    return sequence_info


def generate_frames(csv_file, output_dir, width, height, aa, workers, fps,
                    restart=False, adopt_existing=False):
    try:
        import skia  # Check dependency before modifying output.
    except ImportError as exc:
        raise ValueError("Install dependencies: python3 -m pip install -r requirements.txt") from exc
    if width <= 0 or height <= 0:
        raise ValueError("Width and height must be positive")
    if not 1 <= aa <= 8:
        raise ValueError("AA must be between 1 and 8")
    if workers < 1:
        raise ValueError("Workers must be at least 1")
    if not math.isfinite(fps) or not 0 < fps <= 240:
        raise ValueError("FPS must be greater than 0 and at most 240")
    if restart and adopt_existing:
        raise ValueError("Choose --restart OR --adopt-existing, not both")

    values = read_csv_rows(csv_file)
    total_frames = max(values) + 1
    settings = manifest_settings(csv_file, width, height, aa)
    prepare_output(output_dir, settings, restart, adopt_existing)

    print("Rendering Speed Frames")
    print("Backend: skia")
    print(f"Resolution: {width} x {height} | AA: {aa}x | {fps:g} fps | Workers: {workers}")
    print(f"Output: {output_dir}")
    print("Press Ctrl+C once to stop safely; rerun the same command to resume.")

    # Count only valid, already-finished PNG files. Invalid/corrupt ones are
    # rendered again. Missing frame numbers retain their preceding value.
    completed_frames = set()
    for frame in range(total_frames):
        png = output_dir / f"speed_{frame:06d}.png"
        if valid_frame_png(png, width, height):
            completed_frames.add(frame)
    skipped = len(completed_frames)
    print(f"Found {skipped:,} completed frames; {total_frames - skipped:,} remaining.")
    show_progress(skipped, total_frames)
    start_time = time.perf_counter()

    if skipped < total_frames:
        tasks = (task for task in frame_tasks(values) if task[0] not in completed_frames)
        try:
            if workers == 1:
                # Direct worker mode, with no process startup overhead.
                init_worker(output_dir, width, height, aa)
                completed_now = 0
                for task in tasks:
                    render_task(task)
                    completed_now += 1
                    show_progress(skipped + completed_now, total_frames)
            else:
                # Bound queue size so high AA cannot fill RAM with futures.
                with ProcessPoolExecutor(
                    max_workers=workers,
                    initializer=init_worker,
                    initargs=(output_dir, width, height, aa),
                ) as pool:
                    pending = set()

                    def enqueue_one():
                        try:
                            task = next(tasks)
                        except StopIteration:
                            return False
                        pending.add(pool.submit(render_task, task))
                        return True

                    for _ in range(min(workers * 2, total_frames - skipped)):
                        enqueue_one()

                    completed_now = 0
                    while pending:
                        done, pending = wait(pending, return_when=FIRST_COMPLETED)
                        for future in done:
                            future.result()  # Propagate worker failures.
                            completed_now += 1
                            show_progress(skipped + completed_now, total_frames)
                            enqueue_one()
        except KeyboardInterrupt:
            print("\nStopping: allowing in-progress frames to finish and save...")
            # Exiting the pool context waits for submitted workers. These
            # committed images will be detected on the next run.
            print("Paused. Run the same command later to resume.")
            return False
        finally:
            print()  # Move off the progress bar line.

    seconds = time.perf_counter() - start_time
    sequence_info = write_sequence_info(output_dir, total_frames, width, height, fps)
    print(f"Render complete! {total_frames:,} frames available ({skipped:,} reused) in {seconds:.1f}s.")
    print(f"Playback: {fps:g} fps | Duration: {total_frames / fps:.3f} seconds")
    print(f"Settings saved to: {sequence_info}")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Render antialiased speed PNGs with Skia and resumable multiprocessing for Apple Motion."
    )
    parser.add_argument("csv_path", help="Path to CSV with 'speed' or 'value' column")
    parser.add_argument("-o", "--output", help="Output folder (default: speed_frames beside CSV)")
    parser.add_argument("--width", type=int, default=WIDTH)
    parser.add_argument("--height", type=int, default=HEIGHT)
    parser.add_argument("--aa", type=int, default=AA, help="Supersampling scale 1-8 (default: 1, native antialiasing)")
    parser.add_argument("--fps", type=float, default=FPS, help="Intended image-sequence playback FPS (default: 30). Set same FPS in Apple Motion.")
    parser.add_argument("--restart", action="store_true", help="Delete this renderer's existing PNG frames and start again")
    parser.add_argument("--adopt-existing", action="store_true", help="Resume older speed PNG files with no manifest; use only if CSV/settings match")
    default_workers = (os.cpu_count() or 1) + 1
    parser.add_argument(
        "--workers", type=int, default=default_workers,
        help=f"Parallel render processes (default: CPU core count + 1 = {default_workers}; "
             "override to reduce memory use)",
    )
    args = parser.parse_args()

    csv_file = Path(args.csv_path).expanduser().resolve()
    if not csv_file.is_file():
        parser.error(f"CSV file not found: {csv_file}")
    output_dir = (
        Path(args.output).expanduser().resolve()
        if args.output
        else csv_file.parent / "speed_frames"
    )
    try:
        finished = generate_frames(
            csv_file, output_dir, args.width, args.height, args.aa,
            args.workers, args.fps, args.restart, args.adopt_existing,
        )
    except ValueError as exc:
        parser.error(str(exc))
    if not finished:
        sys.exit(130)


if __name__ == "__main__":
    main()
