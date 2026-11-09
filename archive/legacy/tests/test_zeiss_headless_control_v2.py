"""Mock-only tests; do not need pycromanager or any microscope."""
import io
import unittest
from contextlib import redirect_stdout
from zeiss_headless_control_v2 import ZeissConsole, number


class FakeVector:
    def __init__(self, values):
        self.values = values

    def size(self):
        return len(self.values)

    def get(self, i):
        return self.values[i]


class FakeCore:
    def __init__(self):
        self.writes = []

    def get_loaded_devices(self):
        return FakeVector([
            'ZeissFocusAxis', 'ZeissXYStage', 'ZeissObjectiveTurret',
            'ZeissSidePort', 'ZeissHalogenLamp',
        ])

    def get_position(self, device):
        return 100.0

    def get_x_position(self, device):
        return 500.0

    def get_y_position(self, device):
        return 600.0

    def set_position(self, *args):
        self.writes.append(('move-z',) + args)

    def set_relative_position(self, *args):
        self.writes.append(('jog-z',) + args)

    def set_xy_position(self, *args):
        self.writes.append(('move-xy',) + args)

    def set_relative_xy_position(self, *args):
        self.writes.append(('jog-xy',) + args)

    def set_state(self, *args):
        self.writes.append(('set-state',) + args)

    def set_state_label(self, *args):
        self.writes.append(('set-label',) + args)

    def set_property(self, *args):
        self.writes.append(('set-property',) + args)

    def set_shutter_open(self, *args):
        self.writes.append(('shutter',) + args)

    def stop(self, *args):
        self.writes.append(('stop',) + args)

    def wait_for_device(self, *args):
        self.writes.append(('wait',) + args)

    def get_state_labels(self, device):
        return FakeVector(['1-100%L', '2-50%BP_50%L'])

    def get_state(self, device):
        return 0

    def get_state_label(self, device):
        return '1-100%L'


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.core = FakeCore()
        self.console = ZeissConsole(self.core)

    def run_command(self, command):
        with redirect_stdout(io.StringIO()):
            self.console.dispatch(command)

    def test_z_direct(self):
        self.run_command('move-z 110')
        self.run_command('jog-z -1.25')
        self.assertEqual(self.core.writes, [
            ('move-z', 'ZeissFocusAxis', 110.0),
            ('jog-z', 'ZeissFocusAxis', -1.25),
        ])

    def test_xy_direct(self):
        self.run_command('move-xy 350 450')
        self.run_command('jog-xy 8 -9')
        self.assertEqual(self.core.writes, [
            ('move-xy', 'ZeissXYStage', 350.0, 450.0),
            ('jog-xy', 'ZeissXYStage', 8.0, -9.0),
        ])

    def test_state_and_property(self):
        self.run_command('set-state ZeissObjectiveTurret 2')
        self.run_command('set-label ZeissSidePort "1-100%L"')
        self.run_command('set-property ZeissHalogenLamp "Lamp Intensity" 50')
        self.assertEqual(self.core.writes, [
            ('set-state', 'ZeissObjectiveTurret', 2),
            ('set-label', 'ZeissSidePort', '1-100%L'),
            ('set-property', 'ZeissHalogenLamp', 'Lamp Intensity', '50'),
        ])

    def test_shutter_stop_wait(self):
        self.run_command('shutter ZeissHalogenLamp close')
        self.run_command('stop ZeissXYStage')
        self.run_command('wait ZeissFocusAxis')
        self.assertEqual(self.core.writes, [
            ('shutter', 'ZeissHalogenLamp', False),
            ('stop', 'ZeissXYStage'),
            ('wait', 'ZeissFocusAxis'),
        ])

    def test_nan_rejected(self):
        self.run_command('move-z nan')
        self.assertEqual(self.core.writes, [])

    def test_unknown_device_rejected(self):
        self.run_command('set-state UnknownDevice 1')
        self.assertEqual(self.core.writes, [])

    def test_vector_conversion(self):
        self.assertIn('ZeissXYStage', self.console.devices)

    def test_numeric(self):
        self.assertEqual(number('2.5'), 2.5)
        with self.assertRaises(ValueError):
            number('inf')


if __name__ == '__main__':
    unittest.main()
