"""Offline, no-hardware checks for stage safety checks (stdlib only)."""
import io
import unittest
from contextlib import redirect_stdout
from zeiss_headless_control_v1 import ZeissConsole, validated_bounds


class FakeVector:
    def __init__(self, names):
        self.names = names

    def size(self):
        return len(self.names)

    def get(self, i):
        return self.names[i]


class FakeCore:
    def __init__(self):
        self.z = 100.0
        self.x, self.y = 100.0, 150.0
        self.writes = []

    def get_loaded_devices(self):
        return FakeVector(['ZeissFocusAxis', 'ZeissXYStage'])

    def get_position(self, label):
        return self.z

    def get_x_position(self, label):
        return self.x

    def get_y_position(self, label):
        return self.y

    def set_position(self, label, value):
        self.writes.append(('z', label, value))
        self.z = value

    def set_xy_position(self, label, x, y):
        self.writes.append(('xy', label, x, y))
        self.x, self.y = x, y


class TestSafety(unittest.TestCase):
    def new_console(self, **kwargs):
        self.core = FakeCore()
        return ZeissConsole(self.core, input_fn=lambda prompt: 'MOVE', **kwargs)

    def invoke(self, console, command):
        with redirect_stdout(io.StringIO()):
            console.dispatch(command)

    def test_read_only_blocks_motion(self):
        c = self.new_console(z_limits=(95, 105), xy_limits=(95, 105, 145, 155))
        self.invoke(c, 'jog-z 0.5')
        self.assertFalse(self.core.writes)

    def test_missing_axis_bounds_blocks_motion(self):
        c = self.new_console(enable_motion=True, xy_limits=(80, 120, 140, 160))
        self.invoke(c, 'jog-z 0.5')
        self.assertFalse(self.core.writes)

    def test_z_out_of_bounds_blocks(self):
        c = self.new_console(enable_motion=True, z_limits=(99, 101))
        self.invoke(c, 'jog-z 2')
        self.assertFalse(self.core.writes)

    def test_z_excessive_displacement_blocks(self):
        c = self.new_console(enable_motion=True, z_limits=(90, 110))
        self.invoke(c, 'jog-z 1.5')
        self.assertFalse(self.core.writes)

    def test_z_small_move_allowed(self):
        c = self.new_console(enable_motion=True, z_limits=(99, 102))
        self.invoke(c, 'jog-z 0.2')
        self.assertEqual(self.core.writes, [('z', 'ZeissFocusAxis', 100.2)])

    def test_xy_small_move_allowed(self):
        c = self.new_console(enable_motion=True, xy_limits=(95, 105, 145, 155))
        self.invoke(c, 'jog-xy 3 4')
        self.assertEqual(self.core.writes, [('xy', 'ZeissXYStage', 103.0, 154.0)])

    def test_xy_distance_excessive_blocks(self):
        c = self.new_console(enable_motion=True, xy_limits=(80, 120, 130, 170))
        self.invoke(c, 'jog-xy 9 9')
        self.assertFalse(self.core.writes)

    def test_bad_numeric_blocks(self):
        c = self.new_console(enable_motion=True, z_limits=(90, 110))
        self.invoke(c, 'jog-z nan')
        self.assertFalse(self.core.writes)

    def test_confirmation_rejected_blocks(self):
        self.core = FakeCore()
        c = ZeissConsole(self.core, enable_motion=True, z_limits=(90, 110),
                         input_fn=lambda prompt: 'NO')
        self.invoke(c, 'jog-z 0.2')
        self.assertFalse(self.core.writes)

    def test_bad_bounds_rejected(self):
        with self.assertRaises(ValueError):
            validated_bounds([10, 9], 'Z')


if __name__ == '__main__':
    unittest.main()
