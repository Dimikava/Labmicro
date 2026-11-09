"""Zeiss Axio Observer Z1 + PixeLINK v7 experiment controller.

Windows 7 SP1 x64 / Python 3.8.10 / existing pycromanager Java-headless backend.
The Micro-Manager GUI must be closed before starting the headless instance.

Axes are specified by HOME -> STEP -> ENDPOINT (not v5 interval+distance).
Existing v5 movement/home/sequence commands and v6 camera commands are retained.
"""
import argparse
import gc
import itertools
import json
from pathlib import Path
import sys
import traceback

from . import camera as v6
from . import manual as v5
from . import planner
from .backend import JavaHeadlessBackend
from .console import ZeissConsoleV7, HELP
from .store import Experiments


def offline_preview(experiments, name, count):
    spec = experiments.get(name)
    print(json.dumps(planner.summary(spec), indent=2))
    for event in itertools.islice(planner.iter_events(spec), count):
        print('{:>7d}: {} T={} C={} XYZ={}'.format(
            event['index'], event['region_home'], event['time_index'],
            event['channel_name'], event['xyz_um']))
    print('Offline only: no microscope devices were opened.')


def main(argv=None):
    base = Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(description='Zeiss headless v7: home -> step -> endpoint acquisition')
    p.add_argument('--mm-dir', default=r'C:\Program Files\Micro-Manager-2.0')
    p.add_argument('--config', default=str(base / 'zeiss_camera_working.cfg'),
                   help='The CURRENT working Zeiss + PixeLINK hardware config')
    p.add_argument('--positions', default=str(base / 'zeiss_user_homes.json'))
    p.add_argument('--sequences', default=str(base / 'zeiss_sequences.json'))
    p.add_argument('--experiments', default=str(base / 'zeiss_experiments_v7.json'))
    p.add_argument('--command', default=None, help='Run one console command and exit')
    p.add_argument('--offline-plan', default=None, metavar='NAME',
                   help='Preview a saved v7 experiment without loading Micro-Manager')
    p.add_argument('--debug', action='store_true')
    args = p.parse_args(argv)
    homes = v5.HomeStore(args.positions)
    experiments = Experiments(args.experiments, homes)
    if args.offline_plan is not None:
        offline_preview(experiments, args.offline_plan, 20)
        return 0
    config = v6.resolve_config_path(args.config, base)
    mm_dir = v6.resolve_config_path(args.mm_dir, base)
    if not Path(mm_dir).is_dir():
        print('Micro-Manager installation not found: {}'.format(mm_dir))
        return 2
    if not Path(config).is_file():
        print('Hardware configuration not found: {}'.format(config))
        print('Export the CURRENT camera+Zeiss .cfg from the working Micro-Manager GUI.')
        return 2
    print('Micro-Manager:', mm_dir)
    print('Configuration:', config)
    labels, px, role = v6.describe_config(config)
    print('Configuration devices:', len(labels), '| PixeLINK:', px or '(not found)',
          '| Core camera:', role or '(none)')
    try:
        import pycromanager  # noqa: F401 - preflight the existing patched install
    except Exception:
        print('Pycro-Manager import failed; retain the tested Python 3.8 patch in mm38.')
        traceback.print_exc()
        return 3
    backend = JavaHeadlessBackend(mm_dir, config, args.debug)
    core = console = None
    code = 0
    try:
        core = backend.start()
        console = ZeissConsoleV7(core, homes,
                                 v5.SequenceStore(args.sequences), experiments)
        print('HEADLESS CONNECTED: {} devices'.format(len(console.hw.devices)))
        print('Core camera:', core.get_camera_device())
        print('Type v7-help for experiments, help for manual controls.')
        if args.command is not None:
            console.dispatch(args.command)
            if console.job is not None and console.job.active():
                console.job.thread.join()
            if console.last_error or (console.job is not None and console.job.error):
                code = 1
        else:
            while True:
                try:
                    line = input('zeiss> ')
                except (EOFError, KeyboardInterrupt):
                    print('\nClosing')
                    break
                if not console.dispatch(line):
                    break
    except Exception:
        print('V7 HEADLESS STARTUP/OPERATION ERROR:')
        traceback.print_exc()
        if not backend.started:
            print('Close Micro-Manager GUI and any existing camera-control applications,')
            print('then retry with the known-working combined .cfg absolute path.')
        code = 1
    finally:
        if console is not None and console.job is not None and console.job.active():
            console.job.cancel()
            print('Cancelling acquisition at next checkpoint before closing Java...')
            console.job.thread.join()
        console = core = None
        gc.collect()
        if backend.started:
            try:
                backend.stop()
            except Exception as exc:
                print('Headless cleanup warning:', exc)
                code = 1
        gc.collect()
    return code


if __name__ == '__main__':
    sys.exit(main())
