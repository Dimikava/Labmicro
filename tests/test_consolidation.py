"""Regression tests for the consolidated package boundary (offline only)."""
import sys
import io
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
import unittest
from unittest.mock import patch


class PackageBoundaryTests(unittest.TestCase):
    def test_legacy_modules_reexport_canonical_symbols(self):
        from zeiss_v7 import camera, manual
        import zeiss_headless_complete_v5 as legacy_v5
        import zeiss_headless_complete_v6_1 as legacy_v6

        self.assertIs(legacy_v5.ZeissHardware, manual.ZeissHardware)
        self.assertIs(legacy_v5.HomeStore, manual.HomeStore)
        self.assertIs(legacy_v5.ZeissConsole, manual.ZeissConsole)
        self.assertIs(legacy_v6.ZeissConsoleV6, camera.ZeissConsoleV6)
        self.assertIs(legacy_v6.image_array, camera.image_array)
        self.assertIs(legacy_v6.resolve_config_path, camera.resolve_config_path)

    def test_legacy_entrypoints_keep_root_configuration_defaults(self):
        from zeiss_v7 import camera, manual
        import zeiss_headless_complete_v5 as legacy_v5
        import zeiss_headless_complete_v6_1 as legacy_v6

        project_root = Path(__file__).resolve().parent.parent
        with patch.object(manual.Path, 'is_dir', return_value=True), \
                patch.object(manual.Path, 'is_file', return_value=False), \
                redirect_stdout(io.StringIO()) as v5_output:
            self.assertEqual(legacy_v5.main([]), 2)
        self.assertIn(str(project_root / 'zeiss_only.cfg'), v5_output.getvalue())

        with patch.object(camera.Path, 'is_dir', return_value=True), \
                patch.object(camera.Path, 'is_file', return_value=False), \
                redirect_stdout(io.StringIO()) as v6_output:
            self.assertEqual(legacy_v6.main([]), 2)
        self.assertIn(str(project_root / 'zeiss_camera.cfg'), v6_output.getvalue())

    def test_v7_wrapper_runs_packaged_offline_entrypoint(self):
        from zeiss_v7 import app
        import zeiss_headless_complete_v7 as wrapper

        with TemporaryDirectory() as folder:
            homes = Path(folder) / 'homes.json'
            recipes = Path(folder) / 'recipes.json'
            homes.write_text(
                '{"sample": {"x": 1.0, "y": 2.0, "z": 3.0}}',
                encoding='utf-8',
            )
            recipes.write_text(
                '{"scan": {'
                '"version": 7, "name": "scan", "output_root": "D:/data", '
                '"regions": [{"home": "sample", '
                '"home_xyz_um": {"x": 1.0, "y": 2.0, "z": 3.0}, '
                '"axes": {"x": {"step_um": 1.0, "end_um": 1.0}, '
                '"y": {"step_um": 1.0, "end_um": 2.0}, '
                '"z": {"step_um": 1.0, "end_um": 3.0}}}], '
                '"channels": [{"name": "default", "exposure_ms": null, "actions": []}], '
                '"time": {"count": 1, "interval_s": 0.0}, '
                '"repeats": 1, "pattern": "serpentine", "dwell_ms": 0.0, '
                '"format": "npy", "return_home": true}}',
                encoding='utf-8',
            )

            result = wrapper.main([
                '--positions', str(homes),
                '--experiments', str(recipes),
                '--offline-plan', 'scan',
            ])

        self.assertIs(wrapper.main, app.main)
        self.assertIs(wrapper.offline_preview, app.offline_preview)
        self.assertIs(wrapper.ZeissConsoleV7, app.ZeissConsoleV7)
        self.assertIs(wrapper.Experiments, app.Experiments)
        self.assertEqual(wrapper.HELP, app.HELP)
        self.assertEqual(result, 0)


class BackendLifecycleTests(unittest.TestCase):
    def fake_pycromanager(self, events):
        module = ModuleType('pycromanager')

        def start_headless(**kwargs):
            events.append(('start_headless', kwargs))

        def core():
            events.append(('Core',))
            return 'core-object'

        def stop_headless():
            events.append(('stop_headless',))

        module.start_headless = start_headless
        module.Core = core
        module.stop_headless = stop_headless
        return module

    def test_backend_preserves_java_startup_arguments_and_order(self):
        from zeiss_v7.backend import JavaHeadlessBackend

        events = []
        fake = self.fake_pycromanager(events)
        with patch.dict(sys.modules, {'pycromanager': fake}):
            backend = JavaHeadlessBackend('C:/Program Files/Micro-Manager-2.0', 'working.cfg', True)
            core = backend.start()
            backend.stop()

        self.assertEqual(core, 'core-object')
        self.assertEqual(events, [
            ('start_headless', {
                'mm_app_path': 'C:/Program Files/Micro-Manager-2.0',
                'config_file': 'working.cfg',
                'python_backend': False,
                'debug': True,
            }),
            ('Core',),
            ('stop_headless',),
        ])

    def test_backend_does_not_stop_when_startup_fails(self):
        from zeiss_v7.backend import JavaHeadlessBackend

        events = []
        fake = self.fake_pycromanager(events)

        def fail_start(**kwargs):
            events.append(('start_headless', kwargs))
            raise RuntimeError('simulated startup failure')

        fake.start_headless = fail_start
        with patch.dict(sys.modules, {'pycromanager': fake}):
            backend = JavaHeadlessBackend('mm', 'config', False)
            with self.assertRaisesRegex(RuntimeError, 'simulated startup failure'):
                backend.start()
            backend.stop()

        self.assertEqual(events, [
            ('start_headless', {
                'mm_app_path': 'mm',
                'config_file': 'config',
                'python_backend': False,
                'debug': False,
            }),
        ])

    def test_app_stops_java_when_core_construction_fails(self):
        from zeiss_v7.app import main

        events = []
        fake = self.fake_pycromanager(events)

        def fail_core():
            events.append(('Core',))
            raise RuntimeError('simulated Core construction failure')

        fake.Core = fail_core
        with TemporaryDirectory() as folder:
            mm_dir = Path(folder) / 'Micro-Manager-2.0'
            mm_dir.mkdir()
            config = Path(folder) / 'working.cfg'
            config.write_text('', encoding='utf-8')
            with patch.dict(sys.modules, {'pycromanager': fake}), \
                    redirect_stdout(io.StringIO()):
                result = main([
                    '--mm-dir', str(mm_dir),
                    '--config', str(config),
                    '--positions', str(Path(folder) / 'homes.json'),
                    '--experiments', str(Path(folder) / 'experiments.json'),
                    '--command', 'quit',
                ])

        self.assertEqual(result, 1)
        self.assertEqual(events, [
            ('start_headless', {
                'mm_app_path': str(mm_dir),
                'config_file': str(config),
                'python_backend': False,
                'debug': False,
            }),
            ('Core',),
            ('stop_headless',),
        ])


if __name__ == '__main__':
    unittest.main()
