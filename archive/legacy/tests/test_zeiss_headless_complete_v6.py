"""Offline unit tests for v6. Never connects to microscope."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
import zeiss_headless_complete_v5 as v5
import zeiss_headless_complete_v6 as v6


class Tag:
    def __init__(self, pix):
        self.pix = pix

class FakeCore:
    def __init__(self, rgb=False):
        self.rgb = rgb
        self.camera = '422000035'
        self.xy = [0, 0]
        self.z = 0
        self.snap_count = 0
        self.exp = 10
        self.props = {}
        self.states = {}
        self.labels = {}

    def get_loaded_devices(self):
        return ['ZeissXYStage', 'ZeissFocusAxis', 'ZeissScope', 'COM6', self.camera,
                'ZeissHalogenLamp', 'ZeissReflectorTurret']
    def get_camera_device(self): return self.camera
    def get_image_width(self): return 4
    def get_image_height(self): return 3
    def get_image_bit_depth(self): return 8 if self.rgb else 12
    def get_bytes_per_pixel(self): return 4 if self.rgb else 2
    def get_number_of_components(self): return 4 if self.rgb else 1
    def get_exposure(self): return self.exp
    def set_exposure(self, value): self.exp = float(value)
    def snap_image(self): self.snap_count += 1
    def get_tagged_image(self):
        if self.rgb:
            return Tag(np.tile(np.array([1,2,3,4], dtype=np.uint8), 12))
        return Tag(np.arange(12, dtype=np.uint16).copy())
    def set_xy_position(self, dev, x, y): self.xy = [x,y]
    def set_position(self, dev, z): self.z = z
    def get_x_position(self, dev): return self.xy[0]
    def get_y_position(self, dev): return self.xy[1]
    def get_position(self, dev): return self.z
    def wait_for_device(self, dev): pass
    def set_property(self, device, name, value): self.props[(device,name)] = value
    def set_state(self, dev, idx): self.states[dev] = idx
    def set_state_label(self, dev, label): self.labels[dev] = label
    def set_config(self, group, config): self.props[('preset',group)] = config
    def wait_for_system(self): pass


class TestV6(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.homes = v5.HomeStore(self.root / 'homes.json')
        self.homes.set_xyz('sample_A', 100, 200, 300)
        self.seqs = v5.SequenceStore(self.root / 'sequences.json')
        self.seqs.create('scan_A', 'sample_A', ['1', '1', '0'], ['10', '20', '0'], '0', self.homes)
        self.store = v6.ExperimentStore(self.root / 'experiments.json')
        self.store.create('test', 'scan_A', str(self.root/'results'), self.seqs)
        self.core = FakeCore()

    def _make_job(self, **kwargs):
        recipe = self.store.get('test')
        recipe.update(kwargs)
        out = self.root / 'manual_run'
        v6.AcquisitionJob.prepare_new(out, 'test', recipe, self.seqs.get('scan_A'), '422000035')
        return v6.AcquisitionJob(out, lambda: self.core, progress=lambda x: None)

    def test_sequence_points(self):
        self.assertEqual(v5.SequenceStore.metrics(self.seqs.get('scan_A'))[0], 4)

    def test_recipe_store_persists(self):
        again = v6.ExperimentStore(self.root/'experiments.json')
        self.assertEqual(again.get('test')['sequence'], 'scan_A')
        self.assertEqual(again.get('test')['format'], 'npy')

    def test_action_order_validation(self):
        recipe = self.store.get('test')
        recipe['actions'].reverse()
        with self.assertRaises(ValueError): v6.validate_recipe(recipe)

    def test_extras_insert_before_snap(self):
        self.store.add_action('test', {'type': 'property','device':'lamp','name':'V','value':'2'})
        kinds = [a['type'] for a in self.store.get('test')['actions']]
        self.assertLess(kinds.index('property'), kinds.index('snap'))

    def test_image_monochrome_16_preserved(self):
        arr, md = v6.image_array(self.core)
        self.assertEqual(arr.shape, (3,4))
        self.assertEqual(arr.dtype, np.uint16)
        self.assertEqual(arr[2,3], 11)
        self.assertEqual(md['camera_bit_depth'], 12)

    def test_image_rgb_bgra_converted(self):
        arr, md = v6.image_array(FakeCore(rgb=True))
        self.assertEqual(tuple(arr[0,0]), (3,2,1,4))
        self.assertEqual(md['saved_layout'], 'RGBA8_from_BGRA32')

    def test_image_mismatch_rejected(self):
        self.core.get_tagged_image = lambda: Tag(np.zeros(5,dtype=np.uint16))
        with self.assertRaises(ValueError): v6.image_array(self.core)

    def test_npy_atomic_save(self):
        path = self.root/'test.npy'
        v6.save_image_atomic(path, np.arange(12,dtype=np.uint16))
        np.testing.assert_equal(np.load(path), np.arange(12,dtype=np.uint16))
        self.assertFalse((self.root/'test.npy.partial').exists())

    def test_run_end_to_end(self):
        job = self._make_job()
        job.start(wait=True)
        self.assertEqual(job.state, 'completed')
        self.assertEqual(self.core.snap_count, 4)
        manifest = job.read()
        self.assertEqual(manifest['completed'], [0,1,2,3])
        for i in range(4):
            p = v6.path_for_frame(job.folder,i,'npy')
            self.assertTrue(p.is_file())
            self.assertTrue(p.with_suffix('.json').is_file())
        self.assertEqual(len((job.folder/'events.jsonl').read_text().splitlines()),6)
        self.assertEqual(len((job.folder/'frames.csv').read_text().splitlines()),5)

    def test_resume_completed_does_not_resnap(self):
        job = self._make_job()
        job.start(wait=True)
        self.assertEqual(self.core.snap_count, 4)
        again = v6.AcquisitionJob(job.folder,lambda:self.core,progress=lambda x:None)
        again.start(wait=True)
        self.assertEqual(self.core.snap_count, 4)
        self.assertEqual(again.state,'completed')

    def test_resume_detects_missing_committed_file(self):
        job = self._make_job()
        job.start(wait=True)
        v6.path_for_frame(job.folder,0,'npy').unlink()
        again = v6.AcquisitionJob(job.folder,lambda:self.core,progress=lambda x:None)
        again.start(wait=False); again.thread.join()
        self.assertEqual(again.state,'failed')
        self.assertIn('missing',again.error)

    def test_dwell_and_exposure_actions(self):
        self.store.mutate('test', lambda r: r['actions'].insert(3, {'type':'exposure','ms': 22.5}))
        recipe = self.store.get('test')
        out = self.root / 'exp'
        v6.AcquisitionJob.prepare_new(out,'test',recipe,self.seqs.get('scan_A'),self.core.camera)
        job = v6.AcquisitionJob(out,lambda:self.core,progress=lambda x:None)
        job.start(wait=True)
        self.assertEqual(job.state,'completed')
        self.assertEqual(self.core.exp,22.5)

    def test_cancel_immediately(self):
        job = self._make_job()
        job.cancel()
        job.start(wait=False);job.thread.join()
        self.assertEqual(job.state,'cancelled')
        self.assertEqual(self.core.snap_count, 0)

    def test_console_experiment_command(self):
        con = v6.ZeissConsoleV6(self.core,self.homes,self.seqs,self.store)
        self.assertTrue(con.dispatch('camera info'))
        self.assertTrue(con.dispatch('experiment exposure test 12.5'))
        self.assertTrue(con.dispatch('experiment add-state test reflector 1'))
        self.assertTrue(con.dispatch('experiment preview test 2'))
        self.assertEqual(con.last_error,None)

    def test_manifest_snapshots_plan(self):
        job = self._make_job()
        plan = json.loads((job.folder/'plan.json').read_text())
        self.assertEqual(plan['sequence']['origin_um']['z'],300)
        self.assertEqual(plan['camera_at_start'], self.core.camera)


if __name__ == '__main__':
    unittest.main()
