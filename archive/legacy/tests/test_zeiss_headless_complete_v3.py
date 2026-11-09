"""Offline mock tests, requiring neither pycromanager nor physical hardware."""
import io
import unittest
from contextlib import redirect_stdout
from zeiss_headless_complete_v3 import ZeissConsole, ZeissHardware, vec, switch, core_arg


class JavaVector:
    def __init__(self, values):
        self.values = values

    def size(self):
        return len(self.values)

    def get(self, i):
        return self.values[i]


class FakeCore:
    def __init__(self):
        self.calls = []
        self.props = {
            ('ZeissHalogenLamp', 'Intensity'): '30',
            ('ZeissHalogenLamp', 'OnOff'): 'On',
        }

    def get_loaded_devices(self):
        return JavaVector([
            'COM6', 'ZeissScope', 'ZeissFocusAxis', 'ZeissXYStage',
            'ZeissObjectiveTurret', 'ZeissReflectorTurret',
            'ZeissTubeLens', 'ZeissSidePort', 'ZeissBasePort',
            'ZeissExcitationSwitcher', 'ZeissHalogenLamp'])

    def get_device_type(self, dev):
        return 'StateDevice'

    def get_position(self, dev):
        return 123.4

    def get_x_position(self, dev):
        return 55

    def get_y_position(self, dev):
        return 66

    def set_position(self, *args): self.calls.append(('set_position',) + args)
    def set_relative_position(self, *args): self.calls.append(('set_relative_position',) + args)
    def set_xy_position(self, *args): self.calls.append(('set_xy_position',) + args)
    def set_relative_xy_position(self, *args): self.calls.append(('set_relative_xy_position',) + args)
    def set_state(self, *args): self.calls.append(('set_state',) + args)
    def set_state_label(self, *args): self.calls.append(('set_state_label',) + args)
    def set_property(self, *args): self.calls.append(('set_property',) + args)
    def stop(self, *args): self.calls.append(('stop',) + args)
    def home(self, *args): self.calls.append(('home',) + args)
    def wait_for_device(self, *args): self.calls.append(('wait_for_device',) + args)
    def set_origin(self, *args): self.calls.append(('set_origin',) + args)
    def set_origin_xy(self, *args): self.calls.append(('set_origin_xy',) + args)
    def set_shutter_open(self, *args): self.calls.append(('set_shutter_open',) + args)
    def set_auto_shutter(self, *args): self.calls.append(('set_auto_shutter',) + args)
    def set_config(self, *args): self.calls.append(('set_config',) + args)
    def set_timeout_ms(self, *args): self.calls.append(('set_timeout_ms',) + args)

    def get_state(self, dev): return 2
    def get_state_label(self, dev): return '3-Objektiv3'
    def get_state_labels(self, dev): return JavaVector(['1-Objektiv1', '2-Objektiv2', '3-Objektiv3'])
    def get_device_property_names(self, dev):
        return JavaVector(['Intensity', 'OnOff'] if dev == 'ZeissHalogenLamp' else [])
    def get_property(self, dev, name): return self.props[(dev, name)]
    def is_property_read_only(self, dev, name): return False
    def get_allowed_property_values(self, dev, name): return JavaVector(['On','Off'] if name == 'OnOff' else [])
    def has_property_limits(self, dev, name): return name == 'Intensity'
    def get_property_lower_limit(self, dev, name): return 0
    def get_property_upper_limit(self, dev, name): return 100
    def get_property_type(self, dev, name): return 'Integer'
    def is_property_pre_init(self, dev, name): return False
    def get_available_devices(self, lib): return JavaVector(['ZeissScope','ZeissFocusAxis'])
    def get_available_config_groups(self): return JavaVector(['Channel'])
    def get_available_configs(self, group): return JavaVector(['DAPI','FITC'])
    def get_focus_device(self): return 'ZeissFocusAxis'
    def get_xy_stage_device(self): return 'ZeissXYStage'
    def get_camera_device(self): return ''
    def get_shutter_device(self): return ''
    def get_auto_shutter(self): return True
    def get_shutter_open(self, *args): return False
    def device_busy(self, dev): return False
    def get_version_info(self): return '2.0'
    def get_api_version_info(self): return '75'
    def echo(self, *args):
        self.calls.append(('echo',) + args)
        return args


class TestController(unittest.TestCase):
    def setUp(self):
        self.core = FakeCore()
        self.console = ZeissConsole(self.core)

    def run_command(self, command):
        with redirect_stdout(io.StringIO()) as out:
            result = self.console.dispatch(command)
        self.assertIsNone(self.console.last_error, out.getvalue())
        return out.getvalue(), result

    def test_position_read(self):
        self.assertEqual(self.console.hw.get_z(), 123.4)
        self.assertEqual(self.console.hw.get_xy(), (55, 66))

    def test_move_z(self):
        self.run_command('move-z 124.5')
        self.run_command('jog-z -0.5')
        self.assertEqual(self.core.calls, [('set_position', 'ZeissFocusAxis', 124.5),
                                           ('set_relative_position', 'ZeissFocusAxis', -0.5)])

    def test_move_xy(self):
        self.run_command('move-xy 200 300')
        self.run_command('jog-xy 5 -9')
        self.run_command('jog-x 2')
        self.run_command('jog-y 3')
        self.assertEqual(self.core.calls, [('set_xy_position','ZeissXYStage',200.,300.),
                                           ('set_relative_xy_position','ZeissXYStage',5.,-9.),
                                           ('set_relative_xy_position','ZeissXYStage',2.,0.),
                                           ('set_relative_xy_position','ZeissXYStage',0.,3.)])

    def test_states_and_aliases(self):
        self.run_command('objective 2')
        self.run_command('reflector 0')
        self.run_command('side "1-100%L"')
        self.run_command('set-label base "Position-3"')
        self.assertEqual(self.core.calls, [('set_state','ZeissObjectiveTurret',2),
                                           ('set_state','ZeissReflectorTurret',0),
                                           ('set_state_label','ZeissSidePort','1-100%L'),
                                           ('set_state_label','ZeissBasePort','Position-3')])

    def test_lamp_properties(self):
        self.run_command('lamp Intensity 80')
        self.run_command('set-property lamp OnOff On')
        self.assertEqual(self.core.calls, [('set_property','ZeissHalogenLamp','Intensity','80'),
                                           ('set_property','ZeissHalogenLamp','OnOff','On')])

    def test_shutter(self):
        self.run_command('shutter open')
        self.run_command('shutter focus close')
        self.run_command('autoshutter off')
        self.assertEqual(self.core.calls, [('set_shutter_open',True),
                                           ('set_shutter_open','ZeissFocusAxis',False),
                                           ('set_auto_shutter',False)])

    def test_home_zero_stop_wait(self):
        self.run_command('home stage')
        self.run_command('zero-z')
        self.run_command('zero-xy')
        self.run_command('stop focus')
        self.run_command('wait stage')
        self.assertEqual(self.core.calls,[('home','ZeissXYStage'),
                                          ('set_origin','ZeissFocusAxis'),
                                          ('set_origin_xy','ZeissXYStage'),
                                          ('stop','ZeissFocusAxis'),
                                          ('wait_for_device','ZeissXYStage')])

    def test_presets(self):
        self.run_command('preset Channel DAPI')
        self.run_command('timeout 10000')
        self.assertEqual(self.core.calls,[('set_config','Channel','DAPI'),('set_timeout_ms',10000)])

    def test_core_unrestricted(self):
        output, _ = self.run_command('core echo int:3 float:1.5 bool:true str:hello default')
        self.assertIn('Core echo',output)
        self.assertEqual(self.core.calls,[('echo',3,1.5,True,'hello','default')])

    def test_queries(self):
        for cmd in ('devices','available','info focus','status','all','states',
                    'labels objective','state objective','property lamp Intensity',
                    'properties lamp','lamp','busy focus','roles','configs',
                    'configs Channel','version','shutter-status','autoshutter'):
            self.run_command(cmd)

    def test_quit(self):
        _, cont = self.run_command('quit')
        self.assertFalse(cont)

    def test_unknown_command(self):
        with redirect_stdout(io.StringIO()):
            self.console.dispatch('abcde')
        self.assertTrue(self.console.last_error)

    def test_unknown_device(self):
        with redirect_stdout(io.StringIO()):
            self.console.dispatch('set-state unknown 2')
        self.assertTrue(self.console.last_error)

    def test_nonfinite_values_not_sent(self):
        with redirect_stdout(io.StringIO()):
            self.console.dispatch('move-z nan')
        self.assertTrue(self.console.last_error)
        self.assertFalse(self.core.calls)

    def test_helpers(self):
        self.assertEqual(vec(JavaVector(['a','b'])),['a','b'])
        self.assertTrue(switch('on'))
        self.assertEqual(core_arg('float:2.2'),2.2)


if __name__ == '__main__':
    unittest.main()
