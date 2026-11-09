"""PIXeLINK camera and v6.1-compatible experiment orchestration.

Windows 7 SP1 / Python 3.8 / pycromanager 1.0.2 Java backend.
Uses the consolidated :mod:`zeiss_v7.manual` hardware-control layer.
Only use on an attended microscope when physically safe to move/acquire.
"""
import argparse
import copy
import csv
from datetime import datetime, timezone
import gc
import json
import os
from pathlib import Path
import shlex
import sys
import threading
import time
import traceback

from . import manual as v5

EXPERIMENT_HELP = """
V6 CAMERA
  camera info                          Show camera identity, exposure, pixel format
  camera snap FILE.npy|FILE.tif        Capture one full-resolution image

V6 EXPERIMENT RECIPES (v5 saved XYZ sequences are reused)
  experiment create NAME SEQUENCE OUTPUT_FOLDER
  experiment list | show NAME | preview NAME [COUNT]
  experiment exposure NAME MS          Set exposure before each image
  experiment dwell NAME MS             Wait after reaching each point
  experiment add-property NAME DEVICE PROPERTY VALUE
  experiment add-state NAME DEVICE INDEX
  experiment add-label NAME DEVICE LABEL
  experiment add-preset NAME GROUP PRESET
  experiment format NAME npy|tiff      Default: lossless NPY
  experiment clear-extra NAME          Remove recipe actions added beyond move/wait/dwell/snap/save
  experiment delete NAME

V6 RUN CONTROL
  experiment run NAME                  Start a NEW acquisition run
  experiment status                    Show active job status/progress
  experiment pause                     Pause after the current operation
  experiment resume                    Continue a paused active run
  experiment cancel                    Request cancellation after current operation
  experiment continue RUN_FOLDER       Resume an incomplete run using its frozen plan
  experiment runs NAME                 List previous run directories

Sequences specify positions and dwell. Each run stores immutable plan.json,
manifest.json, events.jsonl, frames.csv, per-frame metadata and lossless images.
Interrupted jobs can resume at the first uncommitted frame. Only a single
acquisition worker can control hardware at a time. Camera snapping is sequential
software-triggered acquisition, NOT hardware-synchronized imaging.
""".strip()


class ExperimentStore:
    def __init__(self, path):
        self.path = Path(path)
        self.items = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError('Experiment file must contain an object')
            self.items = data

    def save(self):
        atomic_json(self.path, self.items)

    def get(self, name):
        if name not in self.items:
            raise ValueError('Unknown experiment {!r}'.format(name))
        return copy.deepcopy(self.items[name])

    def create(self, name, sequence, output_root, sequences):
        v5.SequenceStore.sequence_name(name)
        sequences.get(sequence)
        root = str(Path(output_root).expanduser().resolve())
        recipe = {
            'version': 1, 'sequence': sequence, 'output_root': root,
            'format': 'npy',
            'actions': [
                {'type': 'move_xyz'}, {'type': 'wait_idle'},
                {'type': 'dwell', 'ms': None}, {'type': 'snap'}, {'type': 'save'}
            ]
        }
        self.items[name] = recipe
        self.save()
        return copy.deepcopy(recipe)

    def mutate(self, name, fn):
        recipe = self.get(name)
        fn(recipe)
        validate_recipe(recipe)
        self.items[name] = recipe
        self.save()
        return recipe

    def add_action(self, name, action):
        def insert(recipe):
            index = next(i for i, item in enumerate(recipe['actions']) if item['type'] == 'snap')
            recipe['actions'].insert(index, action)
        return self.mutate(name, insert)


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.partial')
    with tmp.open('w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write('\n')
    os.replace(str(tmp), str(path))


def iso_now():
    return datetime.now(timezone.utc).isoformat()


def validate_recipe(recipe):
    if recipe.get('format') not in ('npy', 'tiff'):
        raise ValueError('Format must be npy or tiff')
    kinds = [a.get('type') for a in recipe.get('actions', [])]
    for required in ('move_xyz', 'wait_idle', 'snap', 'save'):
        if kinds.count(required) != 1:
            raise ValueError('Exactly one {} action is required'.format(required))
    if not (kinds.index('move_xyz') < kinds.index('wait_idle') < kinds.index('snap') < kinds.index('save')):
        raise ValueError('Required sequence: move_xyz, wait_idle, snap, save')
    if kinds[-1] != 'save':
        raise ValueError('save must be final action')
    allowed = {'move_xyz', 'wait_idle', 'dwell', 'snap', 'save', 'property',
               'state', 'label', 'preset', 'exposure'}
    if not all(k in allowed for k in kinds):
        raise ValueError('Unsupported action in recipe')
    for a in recipe['actions']:
        k = a['type']
        if k in ('dwell', 'exposure') and a.get('ms') is not None and v5.num(a['ms']) < 0:
            raise ValueError('Negative dwell/exposure not allowed')
        if k == 'property' and not all(a.get(x) for x in ('device', 'name', 'value')):
            raise ValueError('Property action requires device/name/value')
    return recipe


def image_array(core):
    """Capture via documented MMCoreJ snap_image()/get_tagged_image().

    Does not normalize or clip intensity; preserves native values.
    For 4-component RGB32, convert MMCore's little-endian BGRA -> RGBA.
    """
    import numpy as np
    camera = str(core.get_camera_device())
    if not camera or camera.lower() in ('none', 'undefined'):
        raise RuntimeError('No Core camera configured. Save combined .cfg from Micro-Manager GUI.')
    core.snap_image()
    ti = core.get_tagged_image()
    width = int(core.get_image_width())
    height = int(core.get_image_height())
    bpp = int(core.get_bytes_per_pixel())
    components = int(core.get_number_of_components())
    depth = int(core.get_image_bit_depth())
    raw = ti.pix
    if isinstance(raw, (bytes, bytearray, memoryview)):
        data = np.frombuffer(raw, dtype=np.uint8).copy()
    else:
        data = np.asarray(raw)
        if data.dtype.kind not in 'uifb':
            raise TypeError('Unexpected PixeLINK pixel type {}'.format(data.dtype))
        data = np.ascontiguousarray(data)

    expected_bytes = width * height * bpp
    if data.nbytes != expected_bytes:
        raise ValueError('Image byte-count mismatch: got {}, expected {} ({}x{}, {} bytes/px)'.format(
            data.nbytes, expected_bytes, width, height, bpp))

    if components == 1 and bpp == 1:
        arr = data.view(np.uint8).reshape(height, width).copy()
        layout = 'GRAY8'
    elif components == 1 and bpp == 2:
        arr = data.view('<u2').reshape(height, width).copy()
        layout = 'GRAY16'
    elif components == 1 and bpp == 4:
        arr = data.view('<f4').reshape(height, width).copy()
        layout = 'GRAY_FLOAT32'
    elif components == 4 and bpp == 4:
        bgra = data.view(np.uint8).reshape(height, width, 4)
        arr = bgra[:, :, [2, 1, 0, 3]].copy()
        layout = 'RGBA8_from_BGRA32'
    else:
        raise ValueError('Unsupported format: components={}, bytes/pixel={}. No image saved.'.format(
            components, bpp))

    md = {
        'camera': camera, 'width_px': width, 'height_px': height,
        'components': components, 'bytes_per_pixel': bpp,
        'camera_bit_depth': depth, 'saved_layout': layout,
        'dtype': str(arr.dtype), 'shape': list(arr.shape), 'timestamp_utc': iso_now()
    }
    try:
        md['exposure_ms'] = float(core.get_exposure())
    except Exception:
        pass
    try:
        md['pixel_size_um'] = float(core.get_pixel_size_um())
    except Exception:
        pass
    return arr, md


def save_image_atomic(dest, arr):
    """Atomic full-resolution save. TIFF requires tifffile==2023.4.12 on py3.8."""
    import numpy as np
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + '.partial')
    if dest.suffix.lower() == '.npy':
        with tmp.open('wb') as f:
            np.save(f, arr, allow_pickle=False)
    elif dest.suffix.lower() in ('.tif', '.tiff'):
        try:
            import tifffile
        except ImportError:
            raise RuntimeError('TIFF output requires: python -m pip install "tifffile==2023.4.12"')
        photometric = 'rgb' if arr.ndim == 3 else 'minisblack'
        tifffile.imwrite(str(tmp), arr, photometric=photometric, compression=None)
    else:
        raise ValueError('Choose .npy or .tif')
    os.replace(str(tmp), str(dest))
    return dest


def path_for_frame(folder, index, ext):
    return Path(folder) / 'frames' / ('frame_{:06d}.{}'.format(index, ext))


class AcquisitionJob:
    """One worker per session; worker uses its own Core proxy for ZMQ thread isolation."""
    def __init__(self, run_folder, core_factory, progress=print):
        self.folder = Path(run_folder)
        self.core_factory = core_factory
        self.progress = progress
        self.pause_flag = threading.Event()
        self.cancel_flag = threading.Event()
        self.thread = None
        self.error = None
        self.done = threading.Event()
        self.state = 'pending'
        self._guard = threading.Lock()
        self._manifest = None

    @staticmethod
    def prepare_new(folder, name, recipe, sequence, camera):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=False)
        plan = {'name': name, 'created_utc': iso_now(),
                'recipe': copy.deepcopy(recipe), 'sequence': copy.deepcopy(sequence),
                'camera_at_start': str(camera)}
        validate_recipe(plan['recipe'])
        total = v5.SequenceStore.metrics(sequence)[0]
        atomic_json(folder / 'plan.json', plan)
        atomic_json(folder / 'manifest.json', {
            'status': 'pending', 'total': total, 'completed': [],
            'created_utc': iso_now(), 'updated_utc': iso_now(), 'error': None
        })
        (folder / 'frames').mkdir(exist_ok=True)
        return folder

    def read(self):
        with self._guard:
            self._manifest = json.loads((self.folder / 'manifest.json').read_text(encoding='utf-8'))
            return copy.deepcopy(self._manifest)

    def update(self, **changes):
        with self._guard:
            if self._manifest is None:
                self._manifest = json.loads((self.folder / 'manifest.json').read_text(encoding='utf-8'))
            self._manifest.update(changes)
            self._manifest['updated_utc'] = iso_now()
            atomic_json(self.folder / 'manifest.json', self._manifest)
            return copy.deepcopy(self._manifest)

    def rebuild_frames_csv(self):
        manifest = self.read()
        target = self.folder / 'frames.csv'
        temp = target.with_name(target.name + '.partial')
        columns = ['index', 'image', 'target_x_um', 'target_y_um', 'target_z_um',
                   'actual_x_um', 'actual_y_um', 'actual_z_um', 'exposure_ms',
                   'pixel_dtype', 'saved_utc']
        with temp.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            for index in sorted(manifest['completed']):
                match = list((self.folder / 'frames').glob('frame_{:06d}.json'.format(index)))
                if not match:
                    continue
                item = json.loads(match[0].read_text(encoding='utf-8'))
                actual = item['actual_xyz_um']
                target_xyz = item['target_xyz_um']
                writer.writerow({
                    'index': index, 'image': item['image'],
                    'target_x_um': target_xyz['x'], 'target_y_um': target_xyz['y'],
                    'target_z_um': target_xyz['z'], 'actual_x_um': actual['x'],
                    'actual_y_um': actual['y'], 'actual_z_um': actual['z'],
                    'exposure_ms': item['camera'].get('exposure_ms', ''),
                    'pixel_dtype': item['camera']['dtype'], 'saved_utc': item['saved_utc']
                })
        os.replace(str(temp), str(target))

    def event(self, kind, **fields):
        entry = {'timestamp_utc': iso_now(), 'event': kind}
        entry.update(fields)
        with (self.folder / 'events.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(entry, sort_keys=True) + '\n')

    def start(self, wait=False):
        if self.thread is not None:
            raise ValueError('Job already started')
        self.thread = threading.Thread(target=self._work, name='ZeissAcquisition', daemon=False)
        self.thread.start()
        if wait:
            self.thread.join()
            if self.error:
                raise RuntimeError(self.error)
        return self

    def pause(self):
        self.pause_flag.set()

    def resume(self):
        self.pause_flag.clear()

    def cancel(self):
        self.cancel_flag.set()
        self.pause_flag.clear()

    def active(self):
        return self.thread is not None and self.thread.is_alive()

    def _checkpoint(self):
        if self.cancel_flag.is_set():
            raise AcquisitionCancelled('Cancellation requested')
        while self.pause_flag.is_set():
            self.state = 'paused'
            time.sleep(0.05)
            if self.cancel_flag.is_set():
                raise AcquisitionCancelled('Cancellation requested')
        self.state = 'running'

    def _sleep(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self._checkpoint()
            remaining = end - time.monotonic()
            if remaining > 0:
                time.sleep(min(0.05, remaining))

    def _work(self):
        core = None
        try:
            plan = json.loads((self.folder / 'plan.json').read_text(encoding='utf-8'))
            recipe = validate_recipe(plan['recipe'])
            spec = plan['sequence']
            manifest = self.read()
            completed = set(int(i) for i in manifest['completed'])
            total = manifest['total']
            # Create a new ZMQ Core proxy inside the worker, never share main-thread socket.
            core = self.core_factory()
            hw = v5.ZeissHardware(core)
            camera = str(core.get_camera_device())
            if camera not in hw.devices:
                raise RuntimeError('Camera {} is not loaded in the headless config'.format(camera))
            if camera != plan['camera_at_start']:
                raise RuntimeError('Camera differs from original run: {} != {}'.format(camera, plan['camera_at_start']))
            self.update(status='running', error=None)
            self.rebuild_frames_csv()
            self.event('start', previous_completed=len(completed))
            previous = None
            ext = 'npy' if recipe['format'] == 'npy' else 'tif'
            for index, x, y, z in v5.SequenceStore.points(spec):
                self._checkpoint()
                dest = path_for_frame(self.folder, index, ext)
                sidecar = dest.with_suffix('.json')
                if index in completed:
                    if not (dest.is_file() and sidecar.is_file()):
                        raise RuntimeError('Committed frame {} is missing its image or metadata'.format(index))
                    previous = None  # force positioning on next point after skipped frames
                    continue
                if dest.exists() and sidecar.exists():
                    # A crash after atomic image+sidecar saves, before manifest commit.
                    completed.add(index)
                    self.update(completed=sorted(completed))
                    self.event('recovered_frame', index=index)
                    previous = None
                    continue
                if dest.exists() != sidecar.exists():
                    raise RuntimeError('Incomplete frame {} present on disk. Inspect manually before resume.'.format(index))
                img = md = None
                for action in recipe['actions']:
                    self._checkpoint()
                    kind = action['type']
                    if kind == 'move_xyz':
                        if previous is None or previous[:2] != (x, y):
                            hw.move_xy(x, y)
                            hw.wait('stage')
                        if previous is None or previous[2] != z:
                            hw.move_z(z)
                            hw.wait('focus')
                        previous = (x, y, z)
                    elif kind == 'wait_idle':
                        hw.wait('stage'); hw.wait('focus')
                    elif kind == 'dwell':
                        ms = spec['dwell_ms'] if action.get('ms') is None else action['ms']
                        self._sleep(v5.num(ms) / 1000.0)
                    elif kind == 'property':
                        hw.set_property(action['device'], action['name'], action['value'])
                    elif kind == 'state':
                        hw.set_state(action['device'], action['index'])
                        hw.wait(action['device'])
                    elif kind == 'label':
                        hw.set_label(action['device'], action['label'])
                        hw.wait(action['device'])
                    elif kind == 'preset':
                        core.set_config(action['group'], action['preset'])
                        core.wait_for_system()
                    elif kind == 'exposure':
                        core.set_exposure(float(action['ms']))
                    elif kind == 'snap':
                        img, md = image_array(core)
                    elif kind == 'save':
                        if img is None:
                            raise RuntimeError('Save action called without image at index {}'.format(index))
                        actual_x, actual_y = hw.get_xy()
                        actual_z = hw.get_z()
                        optical = {}
                        for alias in v5.STATE_ALIASES:
                            try:
                                if v5.ALIASES[alias] in hw.devices:
                                    index_state, label_state = hw.get_state(alias)
                                    optical[alias] = {'index': index_state, 'label': label_state}
                            except Exception:
                                pass
                        record = {
                            'index': index, 'target_xyz_um': {'x': x, 'y': y, 'z': z},
                            'actual_xyz_um': {'x': actual_x, 'y': actual_y, 'z': actual_z},
                            'camera': md, 'optical_state': optical, 'image': 'frames/' + dest.name,
                            'saved_utc': iso_now()
                        }
                        # Atomic image write followed by atomic metadata write, then commit.
                        save_image_atomic(dest, img)
                        atomic_json(sidecar, record)
                        completed.add(index)
                        self.update(completed=sorted(completed))
                        self.event('frame_saved', index=index, file=str(dest.name),
                                   target_xyz_um=record['target_xyz_um'])
                        self.progress('[{}/{}] captured {} @ XYZ {:.4f}, {:.4f}, {:.4f} um'.format(
                            len(completed), total, dest.name, x, y, z))
                img = md = None
            self.rebuild_frames_csv()
            self.update(status='completed')
            self.state = 'completed'
            self.event('complete', frames=len(completed))
            self.progress('ACQUISITION COMPLETE: {}/{} images'.format(len(completed), total))
        except AcquisitionCancelled as exc:
            self.state = 'cancelled'
            self.update(status='cancelled', error=str(exc))
            self.event('cancelled', message=str(exc))
            self.progress('ACQUISITION CANCELLED: {}'.format(exc))
        except BaseException as exc:
            self.error = '{}: {}'.format(type(exc).__name__, exc)
            self.state = 'failed'
            self.update(status='failed', error=self.error)
            self.event('error', message=self.error)
            self.progress('ACQUISITION FAILED: {}'.format(self.error))
            traceback.print_exc()
        finally:
            try:
                self.rebuild_frames_csv()
            except Exception:
                pass
            core = None
            self.done.set()


class AcquisitionCancelled(Exception):
    pass


def create_run_folder(output_root, name):
    base = Path(output_root) / name
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    for i in range(1000):
        folder = base / '{}_{}{}'.format(stamp, name, '' if i == 0 else '_{:03d}'.format(i))
        if not folder.exists():
            return folder
    raise RuntimeError('Too many runs created in same second')


class ZeissConsoleV6(v5.ZeissConsole):
    def __init__(self, core, homes, sequences, experiments):
        super().__init__(core, homes, sequences)
        self.experiments = experiments
        self.job = None

    def _camera(self):
        name = str(self.core.get_camera_device())
        if not name or name.lower() in ('none', 'undefined') or name not in self.hw.devices:
            raise ValueError('No loaded Core camera. Export an updated combined .cfg from the working MM GUI.')
        return name

    def camera_command(self, args):
        if args == ['info']:
            name = self._camera()
            print('Core camera: {}'.format(name))
            for label, fn in (
                ('exposure_ms', self.core.get_exposure),
                ('width_px', self.core.get_image_width),
                ('height_px', self.core.get_image_height),
                ('bytes_per_pixel', self.core.get_bytes_per_pixel),
                ('components', self.core.get_number_of_components),
                ('bit_depth', self.core.get_image_bit_depth)):
                self.safe(label, fn)
        elif len(args) == 2 and args[0] == 'snap':
            self._camera()
            dest = Path(args[1])
            if dest.suffix.lower() not in ('.npy', '.tif', '.tiff'):
                raise ValueError('Specify .npy or .tif file extension')
            if dest.exists():
                raise ValueError('Image file already exists; choose another name')
            image, md = image_array(self.core)
            save_image_atomic(dest, image)
            atomic_json(dest.with_suffix('.json'), md)
            print('Saved {} ({}, {})'.format(dest, md['shape'], md['dtype']))
        else:
            raise ValueError('Usage: camera info | camera snap FILE.npy|FILE.tif')

    def experiment_command(self, args):
        if not args:
            raise ValueError('Usage: experiment SUBCOMMAND ...')
        sub, rest = args[0].lower(), args[1:]
        if sub == 'status' and not rest:
            if self.job is None:
                print('No acquisition job in this session')
            else:
                m = self.job.read()
                print('Run: {} | status: {} | saved: {}/{} | folder: {}'.format(
                    self.job.state, m['status'], len(m['completed']), m['total'], self.job.folder))
                if m.get('error'):
                    print('Error: {}'.format(m['error']))
            return
        if sub in ('pause', 'resume', 'cancel') and not rest:
            if self.job is None or not self.job.active():
                raise RuntimeError('No active acquisition')
            getattr(self.job, sub)()
            print('Request sent: {} (after current operation)'.format(sub))
            return
        if sub == 'list' and not rest:
            for name, recipe in sorted(self.experiments.items.items()):
                print('{}: sequence={} format={} output={}'.format(
                    name, recipe['sequence'], recipe['format'], recipe['output_root']))
            return
        if sub == 'create' and len(rest) == 3:
            name, sequence, root = rest
            self.experiments.create(name, sequence, root, self.sequences)
            print('Saved experiment {}'.format(name))
            return
        if sub == 'delete' and len(rest) == 1:
            self.experiments.get(rest[0]); del self.experiments.items[rest[0]]
            self.experiments.save(); print('Deleted recipe, not its run files')
            return
        if sub in ('show', 'preview') and len(rest) in (1, 2):
            name = rest[0]
            recipe = self.experiments.get(name)
            spec = self.sequences.get(recipe['sequence'])
            print(json.dumps(recipe, indent=2))
            if sub == 'preview':
                n = int(rest[1]) if len(rest) == 2 else 10
                if n < 1:
                    raise ValueError('Count >= 1')
                for i, x, y, z in __import__('itertools').islice(v5.SequenceStore.points(spec), n):
                    print('{:>6d}: {:.6f}, {:.6f}, {:.6f} um'.format(i, x, y, z))
                print('Total positions:', v5.SequenceStore.metrics(spec)[0])
            return
        if sub == 'format' and len(rest) == 2:
            name, kind = rest
            self.experiments.mutate(name, lambda r: r.update(format=kind.lower()))
            print('Format:', kind)
            return
        if sub in ('exposure', 'dwell') and len(rest) == 2:
            name, raw_ms = rest
            ms = v5.num(raw_ms)
            if ms < 0 or (sub == 'exposure' and ms == 0):
                raise ValueError('Exposure must be positive, dwell nonnegative')
            def set_value(r):
                found = False
                for a in r['actions']:
                    if a['type'] == sub:
                        a['ms'] = ms; found = True
                if not found:
                    k = next(i for i, a in enumerate(r['actions']) if a['type'] == 'snap')
                    r['actions'].insert(k, {'type': sub, 'ms': ms})
            self.experiments.mutate(name, set_value)
            print('{} = {} ms'.format(sub, ms))
            return
        if sub == 'clear-extra' and len(rest) == 1:
            self.experiments.mutate(rest[0], lambda r: r.update(actions=[a for a in r['actions']
                if a['type'] in ('move_xyz', 'wait_idle', 'dwell', 'snap', 'save')]))
            print('Removed additional actions')
            return
        if sub == 'add-property' and len(rest) == 4:
            name, dev, prop, value = rest
            self.experiments.add_action(name, {'type':'property', 'device':dev,'name':prop,'value':value})
            print('Added property action')
            return
        if sub == 'add-state' and len(rest) == 3:
            name, dev, idx = rest
            self.experiments.add_action(name, {'type':'state','device':dev,'index':int(idx)})
            print('Added state action')
            return
        if sub == 'add-label' and len(rest) == 3:
            name, dev, label = rest
            self.experiments.add_action(name, {'type':'label','device':dev,'label':label})
            print('Added label action')
            return
        if sub == 'add-preset' and len(rest) == 3:
            name, group, preset = rest
            self.experiments.add_action(name, {'type':'preset','group':group,'preset':preset})
            print('Added preset action')
            return
        if sub == 'runs' and len(rest) == 1:
            recipe = self.experiments.get(rest[0])
            base = Path(recipe['output_root']) / rest[0]
            for folder in sorted(base.glob('*')) if base.exists() else []:
                if (folder / 'manifest.json').exists():
                    print(folder)
            return
        if sub == 'run' and len(rest) == 1:
            if self.job is not None and self.job.active():
                raise RuntimeError('An acquisition is already running')
            name = rest[0]
            recipe = self.experiments.get(name)
            validate_recipe(recipe)
            spec = self.sequences.get(recipe['sequence'])
            camera = self._camera()
            folder = create_run_folder(recipe['output_root'], name)
            AcquisitionJob.prepare_new(folder, name, recipe, spec, camera)
            self._launch(folder)
            return
        if sub == 'continue' and len(rest) == 1:
            folder = Path(rest[0])
            m = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
            if m['status'] == 'completed':
                raise ValueError('Run already completed')
            self._launch(folder)
            return
        raise ValueError('Unknown experiment command or argument count. Type v6-help.')

    def _launch(self, folder):
        if self.job is not None and self.job.active():
            raise ValueError('An acquisition is already running')
        from pycromanager import Core
        self.job = AcquisitionJob(folder, Core)
        self.job.start(wait=False)
        print('Run started. Use experiment status/pause/resume/cancel. Folder: {}'.format(folder))

    def dispatch(self, raw):
        try:
            words = shlex.split(raw)
        except ValueError as exc:
            self.last_error = str(exc)
            print('COMMAND ERROR:', exc)
            return True
        if not words:
            return True
        cmd, args = words[0].lower(), words[1:]
        self.last_error = None
        try:
            if self.job is not None and self.job.active():
                allowed = (cmd == 'experiment' and args and args[0].lower() in
                           ('status', 'pause', 'resume', 'cancel')) or cmd in ('v6-help', 'help', 'quit', 'exit', 'q')
                if not allowed:
                    raise RuntimeError('Acquisition active; hardware console commands are blocked')
            if cmd == 'v6-help':
                print(EXPERIMENT_HELP)
            elif cmd == 'camera':
                self.camera_command(args)
            elif cmd == 'experiment':
                self.experiment_command(args)
            elif cmd in ('quit', 'exit', 'q') and self.job is not None and self.job.active():
                self.job.cancel()
                print('Cancelling acquisition at next checkpoint...')
                self.job.thread.join()
                return False
            else:
                return super().dispatch(raw)
        except Exception as exc:
            self.last_error = str(exc)
            print('COMMAND ERROR: {}'.format(exc))
        return True


def resolve_config_path(raw, script_dir):
    """Resolve CLI configurations to absolute paths for the Java subprocess.

    If a relative path exists from the command prompt, prefer it;
    otherwise look for it alongside the controller script.
    """
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = path if path.exists() else Path(script_dir) / path
    return str(path.resolve())


def describe_config(path):
    """Read only: identify camera declarations before starting Java."""
    camera_device = None
    camera_role = None
    loaded_labels = []
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        parts = [s.strip() for s in line.split(',')]
        if len(parts) >= 4 and parts[0] == 'Device':
            loaded_labels.append(parts[1])
            if parts[2].lower() == 'pixelink':
                camera_device = parts[1]
        elif len(parts) >= 4 and parts[:3] == ['Property', 'Core', 'Camera']:
            camera_role = parts[3]
    return loaded_labels, camera_device, camera_role


def main(argv=None, base_dir=None):
    folder = Path(base_dir).resolve() if base_dir is not None else Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(description='Headless Zeiss + PixeLINK microscope experiments v6')
    p.add_argument('--mm-dir', default=r'C:\Program Files\Micro-Manager-2.0')
    p.add_argument('--config', default=str(folder / 'zeiss_camera.cfg'))
    p.add_argument('--positions', default=str(folder / 'zeiss_user_homes.json'))
    p.add_argument('--sequences', default=str(folder / 'zeiss_sequences.json'))
    p.add_argument('--experiments', default=str(folder / 'zeiss_experiments.json'))
    p.add_argument('--command', default=None)
    p.add_argument('--debug', action='store_true')
    args = p.parse_args(argv)
    args.mm_dir = resolve_config_path(args.mm_dir, folder)
    args.config = resolve_config_path(args.config, folder)
    if not Path(args.mm_dir).is_dir() or not Path(args.config).is_file():
        print('Check Micro-Manager directory and configuration file:')
        print('  Micro-Manager:', args.mm_dir)
        print('  Configuration:', args.config)
        return 2
    print('Micro-Manager folder:', args.mm_dir)
    print('Configuration (absolute):', args.config)
    try:
        labels, camera_device, camera_role = describe_config(args.config)
        print('Config devices:', len(labels))
        print('PixeLINK declaration:', camera_device or '(not present)')
        print('Core camera role:', camera_role or '(not assigned)')
    except (OSError, UnicodeError) as exc:
        print('Cannot inspect configuration:', exc)
        return 2
    try:
        from pycromanager import Core, start_headless, stop_headless
    except Exception:
        traceback.print_exc()
        print('Check your existing mm38 Pycro-Manager Python 3.8 compatibility patch')
        return 3
    started = False
    core = console = None
    code = 0
    try:
        start_headless(mm_app_path=args.mm_dir, config_file=args.config,
                       python_backend=False, debug=args.debug)
        started = True
        core = Core()
        console = ZeissConsoleV6(core, v5.HomeStore(args.positions),
                                  v5.SequenceStore(args.sequences), ExperimentStore(args.experiments))
        print('HEADLESS CONNECTED: {} devices. v6-help for camera/experiment commands.'.format(
            len(console.hw.devices)))
        print('Core camera:', core.get_camera_device())
        if args.command is not None:
            console.dispatch(args.command)
            if console.job is not None and console.job.active():
                console.job.thread.join()
            if console.last_error or (console.job and console.job.error):
                code = 1
        else:
            while True:
                try:
                    line = input('zeiss> ')
                except (KeyboardInterrupt, EOFError):
                    print('\nExiting')
                    break
                if not console.dispatch(line):
                    break
    except Exception:
        print('V6.1 STARTUP / OPERATION ERROR:')
        traceback.print_exc()
        if not started:
            print('Java failed before connecting. First retry using the known-working')
            print('configuration: --config zeiss_only.cfg --debug')
            print('If that succeeds, export the current Zeiss + PixeLINK configuration')
            print('from the working Micro-Manager GUI and try its ABSOLUTE path.')
        code = 1
    finally:
        if console is not None and console.job is not None and console.job.active():
            console.job.cancel()
            print('Waiting for acquisition to stop before closing Java backend...')
            console.job.thread.join()
        console = None; core = None
        gc.collect()
        if started:
            try:
                stop_headless()
            except Exception as exc:
                print('Shutdown warning:', exc)
                code = 1
        gc.collect()
    return code


if __name__ == '__main__':
    sys.exit(main())
