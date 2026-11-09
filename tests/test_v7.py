import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zeiss_v7 import planner
from zeiss_v7.store import Experiments
from zeiss_v7.runner import Acquisition


class FakeHomes:
    def __init__(self):
        self.data = {'a': {'x': 10.0,'y':20.0,'z':30.0},
                     'b': {'x': 100.0,'y':200.0,'z':300.0}}
    def get_xyz(self, name):
        return self.data[name].copy()


class Vector:
    def __init__(self, items):
        self.items = items
    def size(self):
        return len(self.items)
    def get(self, i):
        return self.items[i]


class Tagged:
    def __init__(self, pix):
        self.pix = pix


class FakeCore:
    def __init__(self, fail_after=None):
        self.x, self.y, self.z = 0., 0., 0.
        self.moves = []
        self.snaps = 0
        self.fail_after = fail_after
        self.exposure = 10.0
        self.settings = []
    def get_loaded_devices(self):
        return Vector(['COM6', 'ZeissScope', 'ZeissXYStage', 'ZeissFocusAxis',
                       '422000035', 'ZeissReflectorTurret'])
    def get_camera_device(self):
        return '422000035'
    def set_xy_position(self, dev, x, y):
        self.moves.append(('xy', float(x), float(y)))
        self.x, self.y = x, y
    def set_position(self, dev, z):
        self.moves.append(('z', float(z)))
        self.z = z
    def wait_for_device(self, dev):
        self.moves.append(('wait', dev))
    def get_x_position(self, dev):
        return self.x
    def get_y_position(self, dev):
        return self.y
    def get_position(self, dev):
        return self.z
    def get_exposure(self):
        return self.exposure
    def set_exposure(self, v):
        self.exposure = v
        self.settings.append(('exposure', v))
    def set_state(self, dev, v):
        self.settings.append(('state', dev, v))
    def get_image_width(self):
        return 3
    def get_image_height(self):
        return 2
    def get_bytes_per_pixel(self):
        return 2
    def get_number_of_components(self):
        return 1
    def get_image_bit_depth(self):
        return 12
    def get_pixel_size_um(self):
        return 0.25
    def snap_image(self):
        if self.fail_after is not None and self.snaps >= self.fail_after:
            raise RuntimeError('simulated camera failure')
        self.snaps += 1
    def get_tagged_image(self):
        import numpy as np
        return Tagged(np.arange(6, dtype='<u2') + self.snaps)


class PlannerTests(unittest.TestCase):
    def test_exact_end_forward(self):
        self.assertEqual(planner.axis_values(1, 2, 8), [1,3,5,7,8])
    def test_exact_end_reverse(self):
        self.assertEqual(planner.axis_values(8, 2, 1), [8,6,4,2,1])
    def test_one_point(self):
        self.assertEqual(planner.axis_values(3, 1, 3), [3])
    def test_zero_invalid(self):
        with self.assertRaises(ValueError):
            planner.axis_values(0, 0, 20)
    def test_nonfinite_invalid(self):
        with self.assertRaises(ValueError):
            planner.axis_values(0, float('nan'), 2)
    def test_serpentine(self):
        spec = planner.new_recipe('s', 'a', FakeHomes().get_xyz('a'), 'output')
        spec['regions'][0]['axes']['x'] = {'step_um': 5, 'end_um': 20}
        spec['regions'][0]['axes']['y'] = {'step_um': 5, 'end_um': 25}
        pts = list(planner.iter_events(spec))
        self.assertEqual([(p['xyz_um']['x'], p['xyz_um']['y']) for p in pts],
                         [(10,20),(15,20),(20,20),(20,25),(15,25),(10,25)])
        self.assertEqual(planner.total_frames(spec), 6)
    def test_full_count(self):
        spec = planner.new_recipe('s', 'a', FakeHomes().get_xyz('a'), 'out')
        spec['regions'][0]['axes']['x'] = {'step_um': 5, 'end_um': 20}
        spec['regions'][0]['axes']['y'] = {'step_um': 5, 'end_um': 25}
        spec['regions'][0]['axes']['z'] = {'step_um': 1, 'end_um': 32}
        spec['regions'].append(planner.new_region('b', FakeHomes().get_xyz('b')))
        spec['channels'].append({'name':'another','exposure_ms':12,'actions':[]})
        spec['time'] = {'count':2, 'interval_s':0.0}
        spec['repeats'] = 2
        self.assertEqual(planner.total_frames(spec), (3*2*3+1)*2*2*2)
    def test_endpoints_raster(self):
        spec = planner.new_recipe('s', 'a', FakeHomes().get_xyz('a'), 'out')
        spec['pattern'] = 'raster'
        spec['regions'][0]['axes']['x'] = {'step_um':3, 'end_um':11}
        spec['regions'][0]['axes']['z'] = {'step_um':2, 'end_um':27}
        pts = list(planner.iter_events(spec))
        self.assertEqual(pts[0]['xyz_um']['x'], 10)
        self.assertEqual(pts[-1]['xyz_um']['z'], 27)
    def test_filename_unique(self):
        s = planner.new_recipe('s','a',FakeHomes().get_xyz('a'),'out')
        s['repeats'] = 2
        self.assertEqual(len({planner.filename(i, 'npy') for i in planner.iter_events(s)}), 2)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Experiments(Path(self.temp.name) / 'experiments.json', FakeHomes())
        self.repo.create('test', 'a', self.temp.name)
    def test_absolute_and_relative(self):
        self.repo.set_axis('test', 'x', 2, 18)
        self.repo.set_axis('test', 'y', 3, -9, relative=True)
        s = self.repo.get('test')
        self.assertEqual(planner.coordinate_axes(s['regions'][0])['x'], [10,12,14,16,18])
        self.assertEqual(planner.coordinate_axes(s['regions'][0])['y'], [20,17,14,11])
    def test_multi_region(self):
        self.repo.add_region('test', 'b')
        self.repo.set_axis('test', 'z', 5, 310, home='b')
        self.assertEqual(planner.total_frames(self.repo.get('test')), 4)
    def test_frozen_home(self):
        self.repo.set_axis('test', 'x', 2, 14)
        self.repo.homes.data['a']['x'] = 900
        self.assertEqual(self.repo.get('test')['regions'][0]['home_xyz_um']['x'], 10)
        self.repo.refresh_home('test', 'a')
        self.assertEqual(self.repo.get('test')['regions'][0]['home_xyz_um']['x'], 900)
    def test_persist(self):
        self.repo.set_time('test',2,1.5)
        reloaded = Experiments(self.repo.path, FakeHomes())
        self.assertEqual(reloaded.get('test')['time'], {'count':2,'interval_s':1.5})
    def test_channel_actions(self):
        self.repo.add_channel('test', 'ch1')
        self.repo.exposure('test', 20, 'ch1')
        self.repo.channel_action('test', 'ch1', {'type':'state', 'device':'ZeissReflectorTurret','index':2})
        self.assertEqual(self.repo.get('test')['channels'][0]['exposure_ms'],20)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.spec = planner.new_recipe('demo', 'a', FakeHomes().get_xyz('a'), self.temp.name)
        self.spec['regions'][0]['axes']['x'] = {'step_um':2.0,'end_um':14.0}
        self.core = FakeCore()
        self.folder = Path(self.temp.name) / 'run'
        Acquisition.prepare(self.folder, self.spec, '422000035')
    def test_acquire_and_csv(self):
        j = Acquisition(self.folder, lambda: self.core, lambda x: None).start(wait=True)
        self.assertEqual(j.read()['status'], 'completed')
        self.assertEqual(self.core.snaps, 3)
        images = list((self.folder / 'images').glob('*.npy'))
        self.assertEqual(len(images),3)
        import numpy as np
        arr = np.load(str(images[0]))
        self.assertEqual(arr.dtype, np.dtype('uint16'))
        self.assertEqual(arr.shape,(2,3))
        self.assertEqual(arr[0,0],1)
        with (self.folder/'frames.csv').open(newline='',encoding='utf-8') as f:
            self.assertEqual(len(list(csv.DictReader(f))),3)
        self.assertEqual([m[0] for m in self.core.moves].count('xy'),3)
    def test_resume_failed_camera_without_duplicates(self):
        self.core.fail_after = 1
        j = Acquisition(self.folder, lambda: self.core, lambda x: None).start()
        j.thread.join()
        self.assertEqual(j.read()['status'],'failed')
        self.assertEqual(len(j.read()['completed']),1)
        self.core.fail_after = None
        j2 = Acquisition(self.folder, lambda: self.core, lambda x: None).start(wait=True)
        self.assertEqual(j2.read()['status'],'completed')
        self.assertEqual(self.core.snaps,3)
        self.assertEqual(len(list((self.folder/'images').glob('*.npy'))),3)
    def test_checksum_detects_changed_data(self):
        Acquisition(self.folder, lambda: self.core, lambda x: None).start(wait=True)
        image = next((self.folder/'images').glob('*.npy'))
        image.write_bytes(b'corrupted')
        job = Acquisition(self.folder, lambda: self.core, lambda x: None).start()
        job.thread.join()
        self.assertEqual(job.read()['status'],'failed')
        self.assertIn('checksum mismatch', job.error)
    def test_camera_identity(self):
        self.core.get_camera_device = lambda: 'other'
        j = Acquisition(self.folder, lambda: self.core, lambda x: None).start()
        j.thread.join()
        self.assertEqual(j.read()['status'],'failed')
    def test_time_channel(self):
        self.spec['time'] = {'count':2,'interval_s':0.0}
        self.spec['channels'] = [{'name':'a','exposure_ms':15, 'actions':[]},
                                 {'name':'b','exposure_ms':20,'actions':[]}]
        folder = Path(self.temp.name)/'multi'
        Acquisition.prepare(folder, self.spec,'422000035')
        job = Acquisition(folder, lambda: self.core, lambda x: None).start(wait=True)
        self.assertEqual(job.read()['total'],12)
        self.assertEqual(self.core.snaps,12)
        self.assertIn(('exposure',15),self.core.settings)
        self.assertIn(('exposure',20),self.core.settings)
    def test_cancelled_job(self):
        job = Acquisition(self.folder, lambda: self.core, lambda x: None)
        job.cancel()
        job.start()
        job.thread.join()
        self.assertEqual(job.read()['status'],'cancelled')
    def test_ome_tiff_when_installed(self):
        try:
            import tifffile
        except ImportError:
            self.skipTest('tifffile not installed')
        self.spec['format']='ome-tiff'
        folder = Path(self.temp.name)/'ome'
        Acquisition.prepare(folder,self.spec,'422000035')
        job = Acquisition(folder, lambda: self.core, lambda x: None).start()
        job.thread.join()
        self.assertEqual(job.read()['status'],'completed',job.error)
        file=next((folder/'images').glob('*.tif'))
        with tifffile.TiffFile(str(file)) as tif:
            self.assertTrue(tif.is_ome)
            self.assertEqual(tif.series[0].shape,(2,3))


if __name__=='__main__':
    unittest.main()

# These tests execute only on the fake Core: no microscope connection.
class ConsoleTests(unittest.TestCase):
    def setUp(self):
        import zeiss_headless_complete_v5 as v5
        from zeiss_v7.console import ZeissConsoleV7
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.core = FakeCore()
        class HomeAdapter:
            def __init__(self):
                self.data = FakeHomes().data
            def get_xyz(self,n): return self.data[n].copy()
        self.homes = HomeAdapter()
        self.repo = Experiments(Path(self.temp.name)/'exp.json', self.homes)
        # legacy sequence store is required by v6 console initialization
        self.console = ZeissConsoleV7(self.core, self.homes,
                        v5.SequenceStore(Path(self.temp.name)/'seq.json'), self.repo)
    def test_cli_experiment_commands(self):
        for cmd in [
            'experiment create test a "{}"'.format(self.temp.name),
            'experiment axis test x 2 15',
            'experiment offset test y 3 -8',
            'experiment axis test z 1 32',
            'experiment channel test fluo',
            'experiment exposure test 30 fluo',
            'experiment channel-state test fluo ZeissReflectorTurret 2',
            'experiment time test 2 0',
            'experiment repeats test 2',
            'experiment pattern test raster',
            'experiment preview test 3',
            'experiment validate test',
        ]:
            self.assertTrue(self.console.dispatch(cmd),cmd)
            self.assertIsNone(self.console.last_error,cmd)
        recipe = self.repo.get('test')
        self.assertEqual(planner.coordinate_axes(recipe['regions'][0])['x'],[10,12,14,15])
        self.assertEqual(planner.coordinate_axes(recipe['regions'][0])['y'],[20,17,14,12])
        self.assertEqual(planner.coordinate_axes(recipe['regions'][0])['z'],[30,31,32])
        self.assertEqual(planner.total_frames(recipe),4*4*3*2*2)
    def test_camera_snap_without_hardware(self):
        self.assertTrue(self.console.dispatch('camera snap "{}/camera.npy"'.format(self.temp.name)))
        self.assertIsNone(self.console.last_error)
        self.assertTrue((Path(self.temp.name)/'camera.npy').is_file())
