"""Crash-resumable step-and-shoot acquisition (Python 3.8, Java headless core)."""
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import traceback

from . import manual as v5
from . import camera as v6
from . import planner
from .store import atomic_json


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def checksum(path):
    sha = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while True:
            chunk = stream.read(4 * 1024 * 1024)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


def create_folder(output_root, name):
    base = Path(output_root) / name
    stem = datetime.now().strftime('%Y%m%d_%H%M%S')
    for i in range(1000):
        folder = base / ('{}_run{}'.format(stem, '' if i == 0 else '_{:03d}'.format(i)))
        if not folder.exists():
            return folder
    raise RuntimeError('No unused run directory available')


def capture_format(dest, image, md, fmt):
    """Preserve original pixels. Optional single-plane OME-TIFF for grayscale images."""
    if fmt in ('npy', 'tiff'):
        v6.save_image_atomic(dest, image)
        return
    if fmt != 'ome-tiff':
        raise ValueError('Unknown image format')
    if image.ndim != 2:
        raise RuntimeError('OME-TIFF output in v7 requires grayscale image. Use TIFF/NPY for RGB(A).')
    try:
        import tifffile
    except ImportError:
        raise RuntimeError('OME-TIFF needs: python -m pip install "tifffile==2023.4.12"')
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_name(dest.name + '.partial')
    metadata = {'axes': 'YX'}
    pixel_um = md.get('pixel_size_um')
    if pixel_um is not None and float(pixel_um) > 0:
        metadata['PhysicalSizeX'] = float(pixel_um)
        metadata['PhysicalSizeY'] = float(pixel_um)
        metadata['PhysicalSizeXUnit'] = 'µm'
        metadata['PhysicalSizeYUnit'] = 'µm'
    tifffile.imwrite(str(temp), image, ome=True, photometric='minisblack',
                     metadata=metadata, compression=None)
    os.replace(str(temp), str(dest))


def frame_target(folder, event, fmt):
    extension = 'npy' if fmt == 'npy' else 'tif'
    return Path(folder) / 'images' / planner.filename(event, extension)


def frame_sidecar(folder, event):
    return Path(folder) / 'metadata' / ('frame_{:08d}.json'.format(event['index']))


class Cancelled(Exception):
    pass


class Acquisition:
    """One acquisition worker; owns an independent ZMQ Core proxy in the worker thread."""
    def __init__(self, folder, core_factory, notify=print):
        self.folder = Path(folder)
        self.core_factory = core_factory
        self.notify = notify
        self.thread = None
        self.pause_flag = threading.Event()
        self.cancel_flag = threading.Event()
        self.done = threading.Event()
        self.state = 'pending'
        self.error = None
        self._lock = threading.Lock()

    @staticmethod
    def prepare(folder, spec, camera):
        planner.validate(spec)
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=False)
        (folder / 'images').mkdir()
        (folder / 'metadata').mkdir()
        atomic_json(folder / 'plan.json', {
            'schema': 7, 'created_utc': utcnow(), 'experiment': planner.freeze(spec),
            'camera_at_start': str(camera), 'total_frames': planner.total_frames(spec),
            'order': 'time, region, z, y, x, channel, repeat',
            'type': 'software-sequenced-step-and-shoot'
        })
        atomic_json(folder / 'manifest.json', {
            'schema': 7, 'status': 'pending', 'completed': [],
            'total': planner.total_frames(spec), 'updated_utc': utcnow(),
            'created_utc': utcnow(), 'error': None
        })
        return folder

    def read(self):
        with self._lock:
            return json.loads((self.folder / 'manifest.json').read_text(encoding='utf-8'))

    def update(self, **fields):
        with self._lock:
            m = json.loads((self.folder / 'manifest.json').read_text(encoding='utf-8'))
            m.update(fields)
            m['updated_utc'] = utcnow()
            atomic_json(self.folder / 'manifest.json', m)
            return m

    def log(self, event, **kwargs):
        with (self.folder / 'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'event': event, 'utc': utcnow(), **kwargs}, sort_keys=True) + '\n')

    def active(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, wait=False):
        if self.thread is not None:
            raise RuntimeError('Acquisition already started')
        self.thread = threading.Thread(target=self._work, name='ZeissV7Acquisition', daemon=False)
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

    def check(self):
        if self.cancel_flag.is_set():
            raise Cancelled('Cancellation requested')
        while self.pause_flag.is_set():
            self.state = 'paused'
            time.sleep(0.05)
            if self.cancel_flag.is_set():
                raise Cancelled('Cancellation requested')
        self.state = 'running'

    def sleep(self, seconds):
        limit = time.monotonic() + seconds
        while time.monotonic() < limit:
            self.check()
            time.sleep(min(0.1, max(0, limit - time.monotonic())))

    def verify_existing(self, event, fmt):
        img = frame_target(self.folder, event, fmt)
        meta = frame_sidecar(self.folder, event)
        if img.exists() != meta.exists():
            raise RuntimeError('Partial acquisition frame {}: inspect {} and {} before resuming'.format(
                event['index'], img, meta))
        if not img.exists():
            return False
        record = json.loads(meta.read_text(encoding='utf-8'))
        if int(record['index']) != event['index']:
            raise RuntimeError('Frame index mismatch in {}'.format(meta))
        if record['image_sha256'] != checksum(img):
            raise RuntimeError('Frame checksum mismatch: {}'.format(img))
        return True

    def rebuild_csv(self):
        fieldnames = ['index','image','utc','region','time_index','channel','repeat',
                      'target_x_um','target_y_um','target_z_um','actual_x_um',
                      'actual_y_um','actual_z_um','exposure_ms','dtype','sha256']
        path = self.folder / 'frames.csv'
        tmp = path.with_name(path.name + '.partial')
        committed = set(int(i) for i in self.read()['completed'])
        with tmp.open('w', newline='', encoding='utf-8') as stream:
            out = csv.DictWriter(stream, fieldnames=fieldnames)
            out.writeheader()
            for index in sorted(committed):
                meta = self.folder / 'metadata' / ('frame_{:08d}.json'.format(index))
                if not meta.is_file():
                    continue
                item = json.loads(meta.read_text(encoding='utf-8'))
                target = item['target_xyz_um']
                actual = item['actual_xyz_um']
                out.writerow({
                    'index': item['index'], 'image': item['image'], 'utc': item['saved_utc'],
                    'region': item['region_home'], 'time_index': item['time_index'],
                    'channel': item['channel'], 'repeat': item['repeat_index'],
                    'target_x_um': target['x'], 'target_y_um': target['y'], 'target_z_um': target['z'],
                    'actual_x_um': actual['x'], 'actual_y_um': actual['y'], 'actual_z_um': actual['z'],
                    'exposure_ms': item['camera'].get('exposure_ms'),
                    'dtype': item['camera'].get('dtype'), 'sha256': item['image_sha256'],
                })
        os.replace(str(tmp), str(path))

    @staticmethod
    def apply_channel(core, hw, channel):
        """Actions are explicit, no unsupported hardware capability is assumed."""
        for item in channel['actions']:
            kind = item['type']
            if kind == 'preset':
                core.set_config(item['group'], item['preset'])
                core.wait_for_system()
            elif kind == 'state':
                hw.set_state(item['device'], int(item['index']))
                hw.wait(item['device'])
            elif kind == 'label':
                hw.set_label(item['device'], item['label'])
                hw.wait(item['device'])
            elif kind == 'property':
                hw.set_property(item['device'], item['property'], str(item['value']))
                hw.wait(item['device'])
            else:
                raise ValueError('Unsupported channel action ' + kind)
        if channel['exposure_ms'] is not None:
            core.set_exposure(float(channel['exposure_ms']))

    def _work(self):
        core = None
        try:
            saved = json.loads((self.folder / 'plan.json').read_text(encoding='utf-8'))
            if saved.get('schema') != 7:
                raise RuntimeError('Not a v7 acquisition')
            spec = saved['experiment']
            planner.validate(spec)
            manifest = self.read()
            if manifest['total'] != planner.total_frames(spec):
                raise RuntimeError('Saved manifest/plan count mismatch')
            complete = set(int(v) for v in manifest['completed'])
            core = self.core_factory()  # New ZMQ proxy in worker thread.
            hw = v5.ZeissHardware(core)
            camera = str(core.get_camera_device())
            if camera not in hw.devices or camera != saved['camera_at_start']:
                raise RuntimeError('Camera differs from plan: {} (expected {})'.format(camera, saved['camera_at_start']))
            self.update(status='running', error=None)
            self.log('start_or_resume', committed=len(complete))
            time_base = time.monotonic()
            last_xy = None
            last_z = None
            last_channel = None
            current_timepoint = None
            for event in planner.iter_events(spec):
                self.check()
                index = event['index']
                if index in complete:
                    if not self.verify_existing(event, spec['format']):
                        raise RuntimeError('Committed frame {} missing on disk'.format(index))
                    last_xy = last_z = last_channel = None  # Do not trust hardware state on resume.
                    continue
                if self.verify_existing(event, spec['format']):
                    # Two-phase commit recovery after image/metadata but before manifest update.
                    complete.add(index)
                    self.update(completed=sorted(complete))
                    self.log('recovered_committed_frame', index=index)
                    last_xy = last_z = last_channel = None
                    continue
                ti = event['time_index']
                if current_timepoint != ti:
                    scheduled = ti * spec['time']['interval_s']
                    elapsed = time.monotonic() - time_base
                    if scheduled > elapsed:
                        self.sleep(scheduled - elapsed)
                    self.log('timepoint_start', time_index=ti,
                             lag_s=max(0.0, time.monotonic() - time_base - scheduled))
                    current_timepoint = ti
                xyz = event['xyz_um']
                xy = (xyz['x'], xyz['y'])
                # Preserve previous v6 behavior: move XY, WAIT, then focus Z, WAIT.
                if last_xy != xy:
                    hw.move_xy(*xy)
                    hw.wait('stage')
                    last_xy = xy
                if last_z != xyz['z']:
                    hw.move_z(xyz['z'])
                    hw.wait('focus')
                    last_z = xyz['z']
                channel = spec['channels'][event['channel_index']]
                if last_channel != event['channel_index']:
                    self.apply_channel(core, hw, channel)
                    last_channel = event['channel_index']
                if spec['dwell_ms'] > 0:
                    self.sleep(spec['dwell_ms'] / 1000.0)
                self.check()
                pixels, cam = v6.image_array(core)
                actual_xy = hw.get_xy()
                actual_z = hw.get_z()
                self.check()
                file = frame_target(self.folder, event, spec['format'])
                meta = frame_sidecar(self.folder, event)
                optical_states = {}
                for alias in v5.STATE_ALIASES:
                    label = v5.ALIASES[alias]
                    if label in hw.devices:
                        try:
                            state, state_label = hw.get_state(alias)
                            optical_states[alias] = {'index': state, 'label': state_label}
                        except Exception:
                            pass  # Not every Zeiss module exposes readable states.
                camera_settings = {}
                try:
                    camera_settings['binning'] = int(core.get_binning())
                except Exception:
                    pass
                try:
                    properties = v5.vec(core.get_device_property_names(camera))
                    for property_name in properties:
                        if property_name.lower() in ('gain', 'pixeltype', 'binning', 'exposure',
                                                     'offset', 'exposuretime', 'roi'):
                            try:
                                camera_settings[property_name] = str(core.get_property(camera, property_name))
                            except Exception:
                                pass
                except Exception:
                    pass
                capture = {
                    'index': index, 'region_index': event['region_index'],
                    'region_home': event['region_home'], 'time_index': ti,
                    'channel': event['channel_name'], 'repeat_index': event['repeat_index'],
                    'x_index': event['x_index'], 'y_index': event['y_index'],
                    'z_index': event['z_index'], 'target_xyz_um': xyz,
                    'actual_xyz_um': {'x': float(actual_xy[0]), 'y': float(actual_xy[1]),
                                      'z': float(actual_z)},
                    'camera': cam, 'camera_settings': camera_settings,
                    'optical_state': optical_states, 'saved_utc': utcnow(),
                    'image': str(file.relative_to(self.folder)).replace('\\', '/'),
                }
                # Do not silently re-use unexpected partial existing frame.
                if file.exists() or meta.exists():
                    raise RuntimeError('Unexpected pre-existing acquisition file at {}'.format(file))
                capture_format(file, pixels, cam, spec['format'])
                capture['image_sha256'] = checksum(file)
                atomic_json(meta, capture)
                complete.add(index)
                self.update(completed=sorted(complete))
                self.log('frame_saved', index=index, image=capture['image'])
                self.notify('[{}/{}] {} @ ({:.4f},{:.4f},{:.4f}) um'.format(
                    len(complete), manifest['total'], file.name, xyz['x'], xyz['y'], xyz['z']))
            if spec.get('return_home') and spec['regions']:
                home = spec['regions'][0]['home_xyz_um']
                hw.move_xy(home['x'], home['y'])
                hw.wait('stage')
                hw.move_z(home['z'])
                hw.wait('focus')
            self.rebuild_csv()
            self.update(status='completed', error=None)
            self.state = 'completed'
            self.log('completed', frames=len(complete))
            self.notify('ACQUISITION COMPLETE: {} frames'.format(len(complete)))
        except Cancelled as exc:
            self.state = 'cancelled'
            self.update(status='cancelled', error=str(exc))
            self.log('cancelled', message=str(exc))
            self.notify('ACQUISITION CANCELLED')
        except BaseException as exc:
            self.state = 'failed'
            self.error = '{}: {}'.format(type(exc).__name__, exc)
            try:
                self.update(status='failed', error=self.error)
                self.log('error', message=self.error)
            except Exception:
                traceback.print_exc()
            self.notify('ACQUISITION FAILED: {}'.format(self.error))
            traceback.print_exc()
        finally:
            try:
                self.rebuild_csv()
            except Exception:
                pass
            core = None
            self.done.set()
