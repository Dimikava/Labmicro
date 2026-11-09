"""Interactive v7 experiment CLI, layered over tested v5/v6 hardware console."""
import csv
import itertools
import json
from pathlib import Path
import shlex

from . import manual as v5
from . import camera as v6
from . import planner
from .store import Experiments, checked_name
from .runner import Acquisition, create_folder

HELP = """
V7 EXPERIMENTS — HOME / STEP / ENDPOINT MODEL (all positions in micrometers)
  mark-home NAME                             Save current XYZ (v5 command)
  experiment create NAME HOME OUTPUT_DIR     Create experiment from named XYZ home
  experiment add-region NAME HOME            Add another named-home acquisition region
  experiment remove-region NAME HOME
  experiment refresh-home NAME HOME          Refresh starting XYZ from saved home
  experiment axis NAME X|Y|Z STEP END [HOME] Absolute END coordinate. STEP is magnitude.
  experiment offset NAME X|Y|Z STEP OFFSET [HOME] End relative to home.
      Both start (home) and exact end coordinate are included; the final step
      is shorter when END is not divisible by STEP. Direction follows END.
  experiment pattern NAME serpentine|raster   X-fastest; Y next; Z outermost
  experiment dwell NAME MILLISECONDS
  experiment repeats NAME N                  Frames per position and channel
  experiment time NAME COUNT INTERVAL_SEC    Timepoints, start-to-start schedule
  experiment channel NAME CHANNEL_NAME       Create new acquisition channel
  experiment remove-channel NAME CHANNEL
  experiment exposure NAME MS [CHANNEL]      Default: all channels
  experiment channel-preset NAME CHANNEL GROUP PRESET
  experiment channel-state NAME CHANNEL DEVICE INDEX
  experiment channel-label NAME CHANNEL DEVICE LABEL
  experiment channel-property NAME CHANNEL DEVICE PROPERTY VALUE
  experiment clear-channel NAME CHANNEL      Clear channel optical actions
  experiment format NAME npy|tiff|ome-tiff   OME-TIFF: 2D grayscale only
  experiment return-home NAME on|off         Return to first home after success
  experiment overlap NAME X_PERCENT Y_PERCENT [HOME]
      Derive XY steps from camera field of view using current pixel size.
      Requires calibrated pixel size and unrotated stage-to-image mapping.
  experiment list | show NAME | validate NAME
  experiment preview NAME [COUNT]            Show planned acquisition events
  experiment export NAME CSV_FILE            Export all planned XYZ positions
  experiment run NAME                        Start a new acquisition
  experiment status | pause | resume | cancel
  experiment continue RUN_FOLDER             Resume frozen, incomplete run
  experiment runs NAME                        List saved runs
  experiment delete NAME                      Delete recipe, not images

V7 CAMERA (inherited from v6.1)
  camera info
  camera snap FILE.npy|FILE.tif

The v5 controls (jog-z, move-xy, objective, lamp-intensity, etc.) remain.
Only the v7 'experiment' commands use the new home-step-endpoint schema.
To use the camera, start with the CURRENT working combined .cfg and close the
Micro-Manager GUI first. No software travel limits or per-step confirmations.
""".strip()


class ZeissConsoleV7(v6.ZeissConsoleV6):
    def __init__(self, core, homes, sequences, experiments):
        super().__init__(core, homes, sequences, experiments)
        self.experiments = experiments

    def experiment_command(self, words):
        if not words:
            raise ValueError('Usage: experiment SUBCOMMAND. Type v7-help.')
        sub = words[0].lower()
        a = words[1:]
        if sub == 'status' and not a:
            if self.job is None:
                print('No experiment job in this session')
            else:
                m = self.job.read()
                print('Status {} / {}/{} frames / run {}'.format(
                    m['status'], len(m['completed']), m['total'], self.job.folder))
                if m.get('error'):
                    print('Last error:', m['error'])
            return
        if sub in ('pause', 'resume', 'cancel') and not a:
            if self.job is None or not self.job.active():
                raise ValueError('No active experiment')
            getattr(self.job, sub)()
            print('{} requested; effective after the current blocking hardware operation'.format(sub))
            return
        if sub == 'list' and not a:
            for name, r in sorted(self.experiments.items.items()):
                print('{}: regions={} channels={} images={} output={}'.format(
                    name, len(r['regions']), len(r['channels']), planner.total_frames(r), r['output_root']))
            return
        if sub == 'create' and len(a) == 3:
            self.experiments.create(*a)
            print('Created {}. All axes initially contain only home; configure with experiment axis.'.format(a[0]))
            return
        if sub == 'delete' and len(a) == 1:
            self.experiments.delete(a[0]); print('Deleted recipe {}'.format(a[0])); return
        if sub == 'add-region' and len(a) == 2:
            self.experiments.add_region(*a); print('Added region {}'.format(a[1])); return
        if sub == 'remove-region' and len(a) == 2:
            self.experiments.delete_region(*a); print('Removed region {}'.format(a[1])); return
        if sub == 'refresh-home' and len(a) == 2:
            self.experiments.refresh_home(*a); print('Refreshed home snapshot {}'.format(a[1])); return
        if sub in ('axis', 'offset') and len(a) in (4, 5):
            name, axis, step, end = a[:4]
            home = a[4] if len(a) == 5 else None
            self.experiments.set_axis(name, axis, step, end, home, relative=sub == 'offset')
            print('Updated {} axis {} from home {}'.format(name, axis.upper(), home or '(first region)'))
            return
        if sub == 'pattern' and len(a) == 2:
            self.experiments.set_value(a[0], 'pattern', a[1].lower()); return
        if sub == 'dwell' and len(a) == 2:
            self.experiments.set_value(a[0], 'dwell_ms', planner.positive_or_zero(a[1], 'dwell')); return
        if sub == 'repeats' and len(a) == 2:
            self.experiments.set_value(a[0], 'repeats', planner.positive_int(a[1], 'repeats')); return
        if sub == 'time' and len(a) == 3:
            self.experiments.set_time(*a); return
        if sub == 'channel' and len(a) == 2:
            self.experiments.add_channel(*a); print('Added channel {}'.format(a[1])); return
        if sub == 'remove-channel' and len(a) == 2:
            self.experiments.delete_channel(*a); return
        if sub == 'exposure' and len(a) in (2, 3):
            self.experiments.exposure(a[0], a[1], a[2] if len(a) == 3 else None); return
        if sub == 'channel-preset' and len(a) == 4:
            self.experiments.channel_action(a[0], a[1], {'type': 'preset', 'group': a[2], 'preset': a[3]}); return
        if sub == 'channel-state' and len(a) == 4:
            self.experiments.channel_action(a[0], a[1], {'type': 'state', 'device': a[2], 'index': int(a[3])}); return
        if sub == 'channel-label' and len(a) == 4:
            self.experiments.channel_action(a[0], a[1], {'type': 'label', 'device': a[2], 'label': a[3]}); return
        if sub == 'channel-property' and len(a) == 5:
            self.experiments.channel_action(a[0], a[1], {'type': 'property', 'device': a[2],
                                                          'property': a[3], 'value': a[4]}); return
        if sub == 'clear-channel' and len(a) == 2:
            self.experiments.clear_channel_actions(*a); return
        if sub == 'format' and len(a) == 2:
            self.experiments.set_value(a[0], 'format', a[1].lower()); return
        if sub == 'return-home' and len(a) == 2:
            self.experiments.set_value(a[0], 'return_home', v5.switch(a[1])); return
        if sub == 'overlap' and len(a) in (3, 4):
            name, ox, oy = a[:3]
            home = a[3] if len(a) == 4 else None
            pix = float(self.core.get_pixel_size_um())
            if pix <= 0:
                raise RuntimeError('Core pixel size is uncalibrated (0 um). Set a PixelSize config first.')
            steps = {}
            for axis, dim, overlap in (('x', int(self.core.get_image_width()), ox),
                                       ('y', int(self.core.get_image_height()), oy)):
                pct = planner.number(overlap, 'overlap percent')
                if not 0 <= pct < 100:
                    raise ValueError('Overlap must be in [0,100) percent')
                steps[axis] = pix * dim * (1 - pct / 100.0)
            for axis in ('x', 'y'):
                spec = self.experiments.get(name)
                r = self.experiments._region(spec, home)
                self.experiments.set_axis(name, axis, steps[axis], r['axes'][axis]['end_um'], home)
            print('XY step estimates: {} um. Assumes axis-aligned calibrated stage/camera mapping.'.format(steps))
            return
        if sub in ('show','validate','preview') and len(a) in (1, 2):
            name = a[0]
            spec = self.experiments.get(name)
            print(json.dumps(planner.summary(spec), indent=2))
            if sub == 'show':
                print(json.dumps(spec, indent=2))
            if sub == 'validate':
                for label in (v5.Z_DEVICE, v5.XY_DEVICE):
                    self.hw.device(label)
                camera = self._camera()
                print('VALID PLAN. Camera: {}. Frames: {}.'.format(camera, planner.total_frames(spec)))
                if spec['format'] == 'ome-tiff':
                    print('OME-TIFF requires grayscale camera output and the tifffile dependency.')
                print('Stage limits not checked or imposed by v7.')
            if sub == 'preview':
                n = int(a[1]) if len(a) == 2 else 15
                if n < 1:
                    raise ValueError('Preview count must be >=1')
                for e in itertools.islice(planner.iter_events(spec), n):
                    p = e['xyz_um']
                    print('#{:06d} region={} T={} C={} XYZ=({:.4f},{:.4f},{:.4f}) um'.format(
                        e['index'], e['region_home'], e['time_index'], e['channel_name'],
                        p['x'], p['y'], p['z']))
            return
        if sub == 'export' and len(a) == 2:
            spec = self.experiments.get(a[0])
            target = Path(a[1])
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('w', encoding='utf-8', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['index', 'region', 'time', 'channel', 'repeat',
                                 'x_index', 'y_index', 'z_index', 'x_um', 'y_um', 'z_um'])
                for e in planner.iter_events(spec):
                    p = e['xyz_um']
                    writer.writerow([e['index'], e['region_home'], e['time_index'],
                        e['channel_name'], e['repeat_index'], e['x_index'], e['y_index'], e['z_index'],
                        p['x'], p['y'], p['z']])
            print('Exported plan {}'.format(target)); return
        if sub == 'runs' and len(a) == 1:
            r = self.experiments.get(a[0]); root = Path(r['output_root']) / a[0]
            for f in sorted(root.glob('*')) if root.exists() else []:
                if (f / 'manifest.json').exists():
                    print(f)
            return
        if sub == 'run' and len(a) == 1:
            self._require_idle()
            spec = self.experiments.get(a[0])
            planner.validate(spec)
            if planner.total_frames(spec) < 1:
                raise RuntimeError('Empty acquisition')
            self.hw.device(v5.Z_DEVICE)
            self.hw.device(v5.XY_DEVICE)
            camera = self._camera()
            folder = create_folder(spec['output_root'], a[0])
            Acquisition.prepare(folder, spec, camera)
            self._launch_v7(folder)
            return
        if sub == 'continue' and len(a) == 1:
            self._require_idle()
            folder = Path(a[0]).resolve()
            manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
            if manifest.get('schema') != 7:
                raise RuntimeError('Only v7 run folders can be resumed by v7')
            if manifest['status'] == 'completed':
                raise RuntimeError('Run already completed')
            self._launch_v7(folder)
            return
        raise ValueError('Unknown v7 experiment command. Type v7-help.')

    def _require_idle(self):
        if self.job is not None and self.job.active():
            raise RuntimeError('An acquisition is already running')

    def _launch_v7(self, folder):
        self._require_idle()
        from pycromanager import Core
        self.job = Acquisition(folder, Core)
        self.job.start(wait=False)
        print('v7 acquisition started in {}. Use experiment status/pause/resume/cancel.'.format(folder))

    def dispatch(self, raw):
        try:
            parts = shlex.split(raw)
        except ValueError as exc:
            print('COMMAND ERROR:', exc)
            self.last_error = str(exc)
            return True
        if parts and parts[0].lower() == 'v7-help':
            self.last_error = None
            print(HELP)
            return True
        # Parent dispatch handles active worker restrictions, camera, old manual controls;
        # it dispatches 'experiment' to our overridden experiment_command.
        return super().dispatch(raw)
