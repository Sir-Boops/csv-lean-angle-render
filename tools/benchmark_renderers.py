#!/usr/bin/env python3
"""Short reproducible end-to-end benchmark; always uses fresh output folders."""
import argparse
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--frames', type=int, default=24)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--workers', type=int, nargs='+', default=[1, 4])
    args = parser.parse_args()
    if args.frames < 1 or args.repeats < 1 or any(w < 1 for w in args.workers):
        parser.error('Frames, repeats and workers must be positive')
    args.output.mkdir(parents=True, exist_ok=False)
    csv_file = args.output / 'benchmark.csv'
    csv_file.write_text('frame,lean_angle\n' + ''.join(
        f'{i},{90 * math.sin(2 * math.pi * i / args.frames):.6f}\n'
        for i in range(args.frames)))
    results = []
    cases = [('pillow', 4), ('skia', 1), ('skia', 4)]
    # Alternate order to reduce systematic warm-cache/thermal ordering effects.
    for repeat in range(args.repeats):
        for workers in args.workers:
            for backend, aa in (cases if repeat % 2 == 0 else list(reversed(cases))):
                output = args.output / f'{backend}-aa{aa}-w{workers}-r{repeat}'
                command = [sys.executable, str(ROOT / 'generate_lean_angle_resume.py'),
                           str(csv_file.resolve()), '--backend', backend, '--aa', str(aa),
                           '--width', '1920', '--height', '1080', '--workers', str(workers),
                           '-o', str(output.resolve())]
                started = time.perf_counter()
                run = subprocess.run(command, capture_output=True, text=True, check=True)
                seconds = time.perf_counter() - started
                files = list(output.glob('lean_*.png'))
                if len(files) != args.frames:
                    raise RuntimeError('Incomplete benchmark output')
                result = dict(backend=backend, aa=aa, workers=workers, repeat=repeat,
                              seconds=seconds, frames=args.frames,
                              bytes=sum(p.stat().st_size for p in files))
                results.append(result)
                (output / 'run.log').write_text(run.stdout + run.stderr)
                print(f'{backend:6} AA={aa} workers={workers} repeat={repeat+1}: '
                      f'{seconds:.3f}s ({args.frames / seconds:.2f} fps)', flush=True)
    import PIL
    import skia
    import generate_lean_angle_resume as renderer
    font = renderer.load_font(170)
    report = dict(platform=platform.platform(), machine=platform.machine(),
                  python=sys.version, pillow=PIL.__version__, skia=skia.__version__,
                  logical_cpus=os.cpu_count(), font=str(font.path),
                  source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                                                        text=True).strip(),
                  resolution=[1920, 1080], includes='CLI startup, CSV, drawing, PNG encoding, '
                  'file writes, manifest and sequence metadata; fresh folders; no repeated-angle cache',
                  runs=results, summary=[])
    for workers in args.workers:
        for backend, aa in cases:
            samples = [r for r in results if (r['backend'], r['aa'], r['workers']) ==
                       (backend, aa, workers)]
            seconds = statistics.median(r['seconds'] for r in samples)
            report['summary'].append(dict(backend=backend, aa=aa, workers=workers,
                                          median_seconds=seconds, fps=args.frames / seconds,
                                          mean_bytes_per_frame=statistics.mean(r['bytes'] for r in samples) / args.frames))
    (args.output / 'results.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    main()
