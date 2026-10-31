"""Integration checks for default Skia rendering and shared resume safeguards."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import generate_lean_angle_resume as renderer


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.csv = self.root / 'input.csv'
        self.csv.write_text('frame,lean_angle\n2,-100\n4,42.5\n6,100\n')
        self.output = self.root / 'frames'

    def tearDown(self):
        self.temp.cleanup()

    def command(self, *extra):
        return [sys.executable, str(ROOT / 'generate_lean_angle_resume.py'), str(self.csv),
                '-o', str(self.output),
                '--width', '320', '--height', '180', '--workers', '1', *extra]

    def run_cli(self, *extra, success=True):
        result = subprocess.run(self.command(*extra), text=True, capture_output=True, timeout=30)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def test_defaults_and_legacy_pillow_resume(self):
        # No backend or AA flags: the approved fast Skia configuration is used.
        result = self.run_cli()
        self.assertIn('Backend: skia', result.stdout)
        settings = json.loads((self.output/'render_manifest.json').read_text())
        self.assertEqual(settings['backend'], 'skia')
        self.assertEqual(settings['aa'], 1)

        # Pillow retains the manifest shape and AA default of the old CLI.
        self.run_cli('--backend', 'pillow', '--restart')
        manifest_file = self.output/'render_manifest.json'
        original_manifest = manifest_file.read_bytes()
        legacy = json.loads(original_manifest)
        self.assertNotIn('backend', legacy)
        self.assertEqual(legacy['aa'], 4)
        frames = {p: p.stat().st_mtime_ns for p in self.output.glob('lean_*.png')}
        result = self.run_cli('--backend', 'pillow')
        self.assertIn('7 reused', result.stdout)
        self.run_cli(success=False)  # Migration must not silently adopt legacy frames.
        self.assertEqual(manifest_file.read_bytes(), original_manifest)
        self.assertEqual(frames, {p: p.stat().st_mtime_ns for p in frames})

    def test_missing_skia_does_not_touch_output(self):
        from unittest.mock import patch
        with patch.dict(sys.modules, {'skia': None}):
            with self.assertRaisesRegex(ValueError, 'pip install -r requirements.txt'):
                renderer.generate_frames(self.csv, self.output, 320, 180, 1, 1, 30)
        self.assertFalse(self.output.exists())

    def test_mapping_transparency_and_resume(self):
        self.run_cli()
        frames = sorted(self.output.glob('lean_*.png'))
        self.assertEqual(len(frames), 7)
        for path in frames:
            self.assertTrue(renderer.valid_frame_png(path, 320, 180))
            with Image.open(path) as image:
                self.assertEqual(image.getpixel((0, 0))[3], 0)
                self.assertGreater(image.getchannel('A').getextrema()[1], 0)
        self.assertEqual(frames[0].read_bytes(), frames[1].read_bytes())
        self.assertEqual(frames[2].read_bytes(), frames[3].read_bytes())
        self.assertEqual(frames[4].read_bytes(), frames[5].read_bytes())
        # Negative and positive endpoint dots fall on their respective sides.
        with Image.open(frames[2]) as left, Image.open(frames[6]) as right:
            self.assertGreater(left.getpixel((110, 94))[3], 200)
            self.assertGreater(right.getpixel((210, 94))[3], 200)
        before = {p: p.stat().st_mtime_ns for p in frames}
        result = self.run_cli('--fps', '60')
        self.assertIn('7 reused', result.stdout)
        self.assertEqual(before, {p: p.stat().st_mtime_ns for p in frames})
        self.assertIn('Intended playback FPS: 60', (self.output/'sequence_info.txt').read_text())
        damaged = frames[4]
        damaged.write_bytes(b'not a PNG')
        result = self.run_cli()
        self.assertIn('6 reused', result.stdout)
        self.assertTrue(renderer.valid_frame_png(damaged, 320, 180))

    def test_manifest_blocks_backend_aa_and_csv_changes(self):
        self.run_cli()
        for flags in [('--backend', 'pillow'), ('--aa', '2')]:
            result = self.run_cli(*flags, success=False)
            self.assertIn('different CSV content or render settings', result.stderr)
        self.csv.write_text('value\n12\n')
        self.run_cli(success=False)
        self.run_cli('--restart')
        self.assertEqual(len(list(self.output.glob('lean_*.png'))), 1)

    def test_adoption_and_atomic_save_failure(self):
        self.run_cli()
        (self.output/'render_manifest.json').unlink()
        self.run_cli(success=False)
        self.run_cli('--adopt-existing')
        renderer.init_worker(self.output, 320, 180, 1, 'skia')
        original = (self.output/'lean_000000.png').read_bytes()
        from unittest.mock import patch
        with patch.object(renderer._WORKER_SKIA, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                renderer.render_task((0, 45))
        self.assertEqual((self.output/'lean_000000.png').read_bytes(), original)
        self.assertEqual(list(self.output.glob('.*.tmp')), [])

    def test_spawn_workers(self):
        # Force macOS's multiprocessing start method on this host as well.
        runner = self.root / 'spawn.py'
        runner.write_text(
            'import multiprocessing, runpy\n'
            'if __name__ == "__main__":\n'
            '    multiprocessing.set_start_method("spawn")\n'
            f'    runpy.run_path({str(ROOT / "generate_lean_angle_resume.py")!r}, run_name="__main__")\n')
        command = self.command('--workers', '2')
        command[1] = str(runner)
        env = os.environ.copy()
        env['PYTHONPATH'] = str(ROOT) + os.pathsep + env.get('PYTHONPATH', '')
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list(self.output.glob('lean_*.png'))), 7)

    @unittest.skipUnless(os.name == 'posix', 'requires POSIX SIGINT')
    def test_interrupt_then_resume(self):
        self.csv.write_text('lean_angle\n' + '20\n' * 150)
        # Redirect output to files so progress output cannot block on a full pipe.
        with (self.root/'stdout').open('w+') as out, (self.root/'stderr').open('w+') as err:
            proc = subprocess.Popen(self.command('--workers', '2', '--width', '640', '--height', '360'),
                                    stdout=out, stderr=err)
            try:
                deadline = time.monotonic() + 20
                while not list(self.output.glob('lean_*.png')):
                    if proc.poll() is not None or time.monotonic() > deadline:
                        self.fail('Renderer did not begin output before interruption')
                    time.sleep(.01)
                proc.send_signal(signal.SIGINT)
                proc.wait(timeout=30)
                self.assertEqual(proc.returncode, 130)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
        before = {p: p.stat().st_mtime_ns for p in self.output.glob('lean_*.png')}
        self.assertTrue(before)
        self.run_cli('--workers', '2', '--width', '640', '--height', '360')
        self.assertEqual(len(list(self.output.glob('lean_*.png'))), 150)
        self.assertEqual(before, {p: p.stat().st_mtime_ns for p in before})

    def test_skia_supersampling(self):
        self.run_cli('--aa', '4')
        self.assertTrue(renderer.valid_frame_png(self.output/'lean_000004.png', 320, 180))
        manifest = json.loads((self.output/'render_manifest.json').read_text())
        self.assertEqual(manifest['backend'], 'skia')
        self.assertEqual(len(manifest['resolved_font_sha256']), 64)


if __name__ == '__main__':
    unittest.main()
