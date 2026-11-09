"""Offline regression tests: never launch hardware."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zeiss_headless_complete_v6_1 as v6


class TestPaths(unittest.TestCase):
    def test_relative_config_in_script_dir(self):
        with TemporaryDirectory() as d:
            f = Path(d) / 'working.cfg'
            f.write_text('Device,COM6,SerialManager,COM6\n', encoding='utf8')
            self.assertEqual(v6.resolve_config_path('working.cfg', d), str(f.resolve()))

    def test_absolute_config(self):
        with TemporaryDirectory() as d:
            f = Path(d) / 'working.cfg'
            f.write_text('something', encoding='utf8')
            self.assertEqual(v6.resolve_config_path(str(f), 'ignored'), str(f.resolve()))

    def test_camera_config_preflight(self):
        with TemporaryDirectory() as d:
            p = Path(d) / 'camera.cfg'
            p.write_text('Device,COM6,SerialManager,COM6\n'
                         'Device,422000035,Pixelink,422000035\n'
                         'Property,Core,Camera,422000035\n', encoding='utf8')
            devices, cam, role = v6.describe_config(p)
            self.assertEqual(devices, ['COM6','422000035'])
            self.assertEqual(cam, '422000035')
            self.assertEqual(role, '422000035')


if __name__ == '__main__':
    unittest.main()
