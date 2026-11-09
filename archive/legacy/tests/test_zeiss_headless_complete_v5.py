"""Offline integration tests for v5, no hardware or pycromanager required."""
import csv
import io
import json
import math
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from zeiss_headless_complete_v5 import HomeStore, SequenceStore, ZeissConsole
from test_zeiss_headless_complete_v5_compat import FakeCore

class TestV5(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.home_path = self.folder / 'zeiss_user_homes.json'
        self.seq_path = self.folder / 'zeiss_sequences.json'
        self.homes = HomeStore(self.home_path)
        self.seqs = SequenceStore(self.seq_path)
        self.core = FakeCore()
        self.console = ZeissConsole(self.core, self.homes, self.seqs)
    def cmd(self, command):
        with redirect_stdout(io.StringIO()) as result:
            self.console.dispatch(command)
        self.assertIsNone(self.console.last_error, result.getvalue())
        return result.getvalue()
    def invalid(self, command):
        with redirect_stdout(io.StringIO()):
            self.console.dispatch(command)
        self.assertIsNotNone(self.console.last_error)
    def test_multiple_homes_capture_persist(self):
        self.cmd('mark-home slide_A')
        self.cmd('set-home slide_B 11 22 33')
        self.cmd('set-home slide_C -12.5 3 8')
        self.assertEqual(HomeStore(self.home_path).get_xyz('slide_A'), {'x':55., 'y':66., 'z':123.4})
        self.assertEqual(HomeStore(self.home_path).get_xyz('slide_B'), {'x':11., 'y':22., 'z':33.})
        self.assertIn('slide_C', self.cmd('homes'))
        self.cmd('go-home slide_B')
        self.assertEqual(self.core.calls[-4:], [
            ('set_xy_position','ZeissXYStage',11.,22.),
            ('wait_for_device','ZeissXYStage'),
            ('set_position','ZeissFocusAxis',33.),
            ('wait_for_device','ZeissFocusAxis')])
        self.cmd('forget-home slide_C')
        self.assertNotIn('slide_C',HomeStore(self.home_path).named())
    def test_legacy_homes_preserved(self):
        self.cmd('mark-home z')
        self.cmd('mark-home xy')
        self.cmd('mark-home specimen')
        saved = HomeStore(self.home_path)
        self.assertEqual(saved.positions['z'], 123.4)
        self.assertEqual(saved.positions['xy'], [55,66])
        self.assertEqual(saved.get_xyz('specimen')['z'], 123.4)
    def test_sequence_creation_and_math(self):
        self.cmd('set-home A 100 200 300')
        self.cmd('sequence create scan1 A 4 2 1 20 -10 5')
        spec = SequenceStore(self.seq_path).get('scan1')
        self.assertEqual(spec['origin_um'],{'x':100.,'y':200.,'z':300.})
        self.assertEqual(spec['steps'],{'x':4,'y':2,'z':1})
        self.assertEqual(spec['distance_um'],{'x':20.,'y':-10.,'z':5.})
        self.assertEqual(SequenceStore.stepsize(spec),{'x':5.,'y':-5.,'z':5.})
        count, length, displacement = SequenceStore.metrics(spec)
        self.assertEqual(count, 30)
        self.assertAlmostEqual(length, 145.0)
        self.assertAlmostEqual(displacement,math.sqrt(525))
        p = list(SequenceStore.points(spec))
        self.assertEqual(len(p),30)
        self.assertEqual(p[:6],[
            (0,100.,200.,300.),(1,105.,200.,300.),(2,110.,200.,300.),
            (3,115.,200.,300.),(4,120.,200.,300.),(5,120.,195.,300.)])
        # Every raster leg changes at most one coordinate and is nearest-neighbour.
        total_distance=0
        for a,b in zip(p,p[1:]):
            differences=[abs(a[i]-b[i]) for i in range(1,4)]
            self.assertLessEqual(sum(v>0 for v in differences),1)
            total_distance+=sum(differences)
        self.assertAlmostEqual(total_distance,length)
        self.assertIn('145.000000 um',self.cmd('sequence show scan1'))
        self.assertIn('first 3 of 30',self.cmd('sequence preview scan1 3'))
        self.assertIn('scan1',self.cmd('sequence list'))
    def test_create_step_size(self):
        self.cmd('set-home specimen 100 150 75')
        self.cmd('sequence create-step scan specimen 2 3 1 -4 5 2 10')
        spec = self.seqs.get('scan')
        self.assertEqual(spec['distance_um'], {'x':-8.,'y':15.,'z':2.})
        self.assertEqual(spec['steps'], {'x':2,'y':3,'z':1})
        self.assertEqual(spec['dwell_ms'], 10.)
        self.assertEqual(SequenceStore.stepsize(spec), {'x':-4.,'y':5.,'z':2.})

    def test_snapshot_and_refresh(self):
        self.cmd('set-home p1 10 20 30')
        self.cmd('sequence create line p1 1 0 0 2 0 0')
        self.cmd('set-home p1 55 66 77')
        self.assertEqual(self.seqs.get('line')['origin_um']['x'], 10.0)
        self.cmd('sequence refresh-home line')
        self.assertEqual(self.seqs.get('line')['origin_um']['x'], 55.0)
        self.cmd('sequence delete line')
        self.assertEqual(SequenceStore(self.seq_path).sequences,{})
    def test_sequence_run_and_wait(self):
        self.cmd('set-home A 1 2 3')
        self.cmd('sequence create s A 1 1 0 5 7 0')
        self.cmd('sequence run s')
        # 4 positions. XY move each; Z set at start only; every move waited.
        moves = [c for c in self.core.calls if c[0] in ('set_position','set_xy_position')]
        self.assertEqual(moves,[
            ('set_xy_position','ZeissXYStage',1.,2.),('set_position','ZeissFocusAxis',3.),
            ('set_xy_position','ZeissXYStage',6.,2.),
            ('set_xy_position','ZeissXYStage',6.,9.),
            ('set_xy_position','ZeissXYStage',1.,9.)])
        self.assertEqual(len([c for c in self.core.calls if c[0]=='wait_for_device']),5)
    def test_sequences_csv(self):
        self.cmd('set-home A 10 20 30')
        self.cmd('sequence create s A 1 0 1 2 0 6')
        out=self.folder/'xyz.csv'
        self.cmd('sequence export s "{}"'.format(out))
        with out.open() as f:
            records=list(csv.DictReader(f))
        self.assertEqual(len(records),4)
        self.assertAlmostEqual(float(records[-1]['cumulative_um']),10.)
    def test_invalid_steps_and_distances(self):
        self.cmd('set-home A 1 2 3')
        self.invalid('sequence create bad A 0 0 0 20 0 0')
        self.invalid('sequence create bad A -1 0 0 20 0 0')
        self.invalid('sequence create bad A 1 0 0 inf 0 0')
        self.invalid('sequence run missing')
        self.assertFalse(self.core.calls)
    def test_round_trip_large_and_zero(self):
        self.cmd('set-home A 1 2 3')
        self.cmd('sequence create stationary A 0 0 0 0 0 0')
        spec = self.seqs.get('stationary')
        self.assertEqual(list(SequenceStore.points(spec)),[(0,1.,2.,3.)])
        self.assertEqual(SequenceStore.metrics(spec)[:2],(1,0.))
    def test_no_autorun_on_load(self):
        self.cmd('set-home p 1 2 3')
        self.cmd('sequence create s p 5 0 0 50 0 0')
        self.cmd('sequence preview s 2')
        self.assertEqual(self.core.calls,[])

if __name__=='__main__': unittest.main()
