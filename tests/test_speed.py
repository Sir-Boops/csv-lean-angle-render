"""Speed-only output, CSV mapping and safe resume integration checks."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import generate_speed_resume as speed


class SpeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.csv = self.root / 'speed.csv'
        self.csv.write_text('frame,speed\n2,72.2\n4,120\n6,72.4\n')
        self.output = self.root / 'frames'

    def tearDown(self):
        self.temp.cleanup()

    def command(self, *extra):
        return [sys.executable, str(ROOT/'generate_speed_resume.py'), str(self.csv),
                '-o', str(self.output), '--width', '320', '--height', '180',
                '--workers', '1', *extra]

    def run_cli(self, *extra, success=True):
        result = subprocess.run(self.command(*extra), capture_output=True, text=True, timeout=30)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def test_centered_number_mapping_and_resume(self):
        self.run_cli()
        frames = sorted(self.output.glob('speed_*.png'))
        self.assertEqual(len(frames), 7)
        for path in frames:
            with Image.open(path) as image:
                self.assertEqual(image.mode, 'RGBA')
                self.assertEqual(image.size, (320, 180))
                alpha = image.getchannel('A')
                left, top, right, bottom = alpha.getbbox()
                self.assertLess(abs((left+right)/2 - 160), 2)
                self.assertLess(abs((top+bottom)/2 - 90), 2)
                self.assertLess(right-left, 100)  # No arc, units or other artwork.
                self.assertLess(bottom-top, 50)
                self.assertEqual(image.getpixel((0, 0))[3], 0)
        self.assertEqual(frames[0].read_bytes(), frames[1].read_bytes())
        self.assertEqual(frames[2].read_bytes(), frames[3].read_bytes())
        self.assertEqual(frames[2].read_bytes(), frames[6].read_bytes())
        self.assertNotEqual(frames[4].read_bytes(), frames[2].read_bytes())
        before = {p: p.stat().st_mtime_ns for p in frames}
        self.assertIn('7 reused', self.run_cli('--fps', '60').stdout)
        self.assertEqual(before, {p: p.stat().st_mtime_ns for p in frames})
        frames[4].write_bytes(b'broken')
        frames[5].unlink()
        self.assertIn('5 reused', self.run_cli().stdout)
        self.assertTrue(speed.valid_frame_png(frames[4], 320, 180))
        self.assertTrue(frames[5].exists())
        self.assertEqual(before[frames[2]], frames[2].stat().st_mtime_ns)

    def test_csv_validation_and_default_output(self):
        self.csv.write_text('value\n0\n72.5\n155\n')
        self.assertEqual(speed.read_csv_rows(self.csv), {0: 0, 1: 72.5, 2: 155})
        command = self.command()
        del command[3:5]  # Use speed_frames beside the CSV.
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root/'speed_frames/speed_000002.png').exists())
        for data in ['value\n-1\n', 'speed\nnan\n', 'speed\ninf\n',
                     'frame,speed\n0,1\n0,2\n', 'frame,speed\n-1,1\n', 'speed\n']:
            self.csv.write_text(data)
            with self.subTest(data=data), self.assertRaises(ValueError):
                speed.read_csv_rows(self.csv)

    def test_manifest_restart_and_adoption(self):
        self.run_cli()
        manifest = self.output/'render_manifest.json'
        self.assertEqual(json.loads(manifest.read_text())['renderer'], 'speed-number')
        self.run_cli('--aa', '2', success=False)
        # A lean manifest must never be silently reused, even in the same folder.
        original = manifest.read_bytes()
        settings = json.loads(original)
        settings.pop('renderer')
        manifest.write_text(json.dumps(settings))
        self.run_cli(success=False)
        manifest.write_bytes(original)
        manifest.unlink()
        self.run_cli(success=False)
        self.assertIn('7 reused', self.run_cli('--adopt-existing').stdout)
        (self.output/'lean_000000.png').write_bytes(b'keep')
        self.csv.write_text('speed\n42\n')
        self.run_cli(success=False)
        self.run_cli('--restart', '--aa', '2')
        self.assertEqual(len(list(self.output.glob('speed_*.png'))), 1)
        self.assertEqual((self.output/'lean_000000.png').read_bytes(), b'keep')
        self.assertTrue(speed.valid_frame_png(self.output/'speed_000000.png', 320, 180))

    def test_atomic_failure(self):
        self.run_cli()
        speed.init_worker(self.output, 320, 180, 1)
        path = self.output/'speed_000000.png'
        before = path.read_bytes()
        with patch.object(speed._WORKER_SKIA, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                speed.render_task((0, 72))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.output.glob('.*.tmp')), [])

    def test_spawn_workers(self):
        runner = self.root/'spawn.py'
        runner.write_text('import multiprocessing, runpy\n'
                          'if __name__ == "__main__":\n'
                          '    multiprocessing.set_start_method("spawn")\n'
                          f'    runpy.run_path({str(ROOT/"generate_speed_resume.py")!r}, run_name="__main__")\n')
        command = self.command('--workers', '2')
        command[1] = str(runner)
        env = os.environ.copy()
        env['PYTHONPATH'] = str(ROOT) + os.pathsep + env.get('PYTHONPATH', '')
        result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list(self.output.glob('speed_*.png'))), 7)

    @unittest.skipUnless(os.name == 'posix', 'requires POSIX SIGINT')
    def test_interrupt_resume(self):
        self.csv.write_text('speed\n' + '72\n'*150)
        with (self.root/'stdout').open('w') as out, (self.root/'stderr').open('w') as err:
            proc = subprocess.Popen(self.command('--width', '640', '--height', '360', '--workers', '2'),
                                    stdout=out, stderr=err)
            try:
                deadline = time.monotonic()+20
                while not list(self.output.glob('speed_*.png')):
                    if proc.poll() is not None or time.monotonic() > deadline:
                        self.fail('Renderer did not begin output')
                    time.sleep(.01)
                proc.send_signal(signal.SIGINT)
                proc.wait(timeout=30)
                self.assertEqual(proc.returncode, 130)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
        before = {p: p.stat().st_mtime_ns for p in self.output.glob('speed_*.png')}
        self.assertTrue(before)
        self.run_cli('--width', '640', '--height', '360', '--workers', '2')
        self.assertEqual(len(list(self.output.glob('speed_*.png'))), 150)
        self.assertEqual(before, {p: p.stat().st_mtime_ns for p in before})


if __name__ == '__main__':
    unittest.main()
