#!/usr/bin/env python3
"""Headless Micro-Manager console for Windows 7 / Python 3.8.

Starts in OFFLINE mode by default. Use --connect to initialize hardware.
Movement/change operations require --enable-writes and per-operation approval.
Does not start Micro-Manager Studio, Java, or a pycro-manager bridge.
"""
import argparse
import os
from pathlib import Path
import shlex
import sys

FILES = {
    'hub': 'zeiss_hub_only.cfg',
    'zeiss': 'zeiss_only.cfg',
    'camera': 'pixelink_only.cfg',
}


def parse_config(path):
    commands, devices = [], []
    for lineno, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        fields = [x.strip() for x in line.split(',')]
        commands.append((lineno, fields))
        if fields[0] == 'Device' and len(fields) >= 4:
            devices.append((fields[1], fields[2], fields[3]))
    return commands, devices


def report_offline(path):
    commands, devices = parse_config(path)
    print('Config:', path)
    print('Commands:', len(commands))
    print('Declared devices:')
    for label, adapter, name in devices:
        print('  {:26s} adapter={:16s} internal={}'.format(label, adapter, name))
    print('No hardware was opened. Use --connect after validating the config.')


def yes(prompt):
    return input(prompt + ' [type YES]: ').strip() == 'YES'


def show_loaded(core):
    print('Loaded devices:')
    for name in core.getLoadedDevices():
        try:
            kind = core.getDeviceType(name)
        except Exception:
            kind = '?'
        print('  {} ({})'.format(name, kind))


def get_status(core):
    show_loaded(core)
    for label in ('ZeissFocusAxis', 'ZeissXYStage'):
        if label not in core.getLoadedDevices():
            continue
        try:
            if label == 'ZeissFocusAxis':
                print('Z (um):', core.getPosition(label))
            else:
                print('XY (um):', core.getXYPosition(label))
        except Exception as exc:
            print('  {} read error: {}'.format(label, exc))
    for label in ('ZeissObjectiveTurret', 'ZeissReflectorTurret',
                  'ZeissTubeLens', 'ZeissSidePort', 'ZeissBasePort',
                  'ZeissExcitationSwitcher'):
        if label in core.getLoadedDevices():
            try:
                print('{}: state={} label={!r}'.format(
                    label, core.getState(label), core.getStateLabel(label)))
            except Exception as exc:
                print('{} read error: {}'.format(label, exc))


def property_report(core, label):
    if label not in core.getLoadedDevices():
        print('Unknown/unloaded device:', label)
        return
    for prop in core.getDevicePropertyNames(label):
        try:
            print('  {} = {!r}'.format(prop, core.getProperty(label, prop)))
        except Exception as exc:
            print('  {} = <error: {}>'.format(prop, exc))


def state_report(core, label):
    if label not in core.getLoadedDevices():
        print('Device not loaded:', label)
        return
    try:
        n = core.getNumberOfStates(label)
        for i in range(n):
            try:
                human = core.getStateLabel(label, i)
            except Exception:
                human = '?'
            print('  {}: {}'.format(i, human))
    except Exception as exc:
        print('Unable to read state list:', exc)


def console(core, can_write):
    print('\nHeadless console. Type help. Moves/writes:', 'ENABLED' if can_write else 'DISABLED')
    while True:
        try:
            line = input('mm> ').strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        try:
            args = shlex.split(line)
            cmd = args[0].lower()
            if cmd in ('exit', 'quit'):
                return
            elif cmd == 'help':
                print('Read-only: status | devices | props DEVICE | states DEVICE')
                print('           z | xy | api | quit')
                print('With --enable-writes: move-z MICRONS | move-xy X_UM Y_UM |')
                print('                      set-state DEVICE INDEX')
                print('With camera loaded: snap FILE.npy')
            elif cmd == 'api':
                print('Core:', core.getVersionInfo())
                print('Device API:', core.getAPIVersionInfo())
            elif cmd == 'devices':
                show_loaded(core)
            elif cmd == 'status':
                get_status(core)
            elif cmd == 'props' and len(args) == 2:
                property_report(core, args[1])
            elif cmd == 'states' and len(args) == 2:
                state_report(core, args[1])
            elif cmd == 'z':
                print('Z (um):', core.getPosition('ZeissFocusAxis'))
            elif cmd == 'xy':
                print('XY (um):', core.getXYPosition('ZeissXYStage'))
            elif cmd == 'snap' and len(args) == 2:
                import numpy as np
                target = Path(args[1]).expanduser().resolve()
                if target.suffix.lower() != '.npy':
                    print('Use a .npy file extension (lossless NumPy array).')
                    continue
                if not target.parent.is_dir():
                    print('Parent directory does not exist.')
                    continue
                if target.exists() and not yes('Overwrite {}?'.format(target)):
                    continue
                core.snapImage()
                frame = core.getImage()
                np.save(str(target), frame)
                print('Saved {} shape={} dtype={}'.format(target, frame.shape, frame.dtype))
            elif cmd in ('move-z', 'move-xy', 'set-state'):
                if not can_write:
                    print('Blocked: launch with --enable-writes to permit changes.')
                    continue
                if cmd == 'move-z' and len(args) == 2:
                    target = float(args[1])
                    if yes('Move Z to {} um? Check sample clearance first.'.format(target)):
                        core.setPosition('ZeissFocusAxis', target)
                        core.waitForDevice('ZeissFocusAxis')
                        print('Z:', core.getPosition('ZeissFocusAxis'))
                elif cmd == 'move-xy' and len(args) == 3:
                    x, y = float(args[1]), float(args[2])
                    if yes('Move XY to ({}, {}) um? Check sample clearance.'.format(x, y)):
                        core.setXYPosition('ZeissXYStage', x, y)
                        core.waitForDevice('ZeissXYStage')
                        print('XY:', core.getXYPosition('ZeissXYStage'))
                elif cmd == 'set-state' and len(args) == 3:
                    label, pos = args[1], int(args[2])
                    if label not in core.getLoadedDevices():
                        print('Not loaded:', label)
                        continue
                    count = core.getNumberOfStates(label)
                    if not 0 <= pos < count:
                        print('Invalid index; valid range is 0..{}'.format(count-1))
                        continue
                    if yes('Change {} to state {}? Check objective and sample clearance.'.format(label, pos)):
                        core.setState(label, pos)
                        core.waitForDevice(label)
                        print('Now:', core.getState(label))
                else:
                    print('Invalid argument count. Type help.')
            else:
                print('Unknown command/arguments. Type help.')
        except Exception as exc:
            print('ERROR:', type(exc).__name__ + ':', exc)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mode', choices=sorted(FILES), default='hub',
                    help='hub, zeiss, or camera (default: hub)')
    ap.add_argument('--mm-dir', default='', help='Existing Micro-Manager installation directory')
    ap.add_argument('--connect', action='store_true',
                    help='Load the selected config into hardware (otherwise offline only)')
    ap.add_argument('--enable-writes', action='store_true',
                    help='Permit movement/state changes in the console, with confirmation')
    args = ap.parse_args()

    cfg = Path(__file__).resolve().parent / FILES[args.mode]
    report_offline(cfg)
    if not args.connect:
        return 0
    if not args.mm_dir:
        ap.error('--mm-dir is required with --connect')
    mm_dir = Path(args.mm_dir).expanduser().resolve()
    if not mm_dir.is_dir():
        ap.error('Micro-Manager directory does not exist: {}'.format(mm_dir))
    if not yes('Hardware initialization can involve mechanical movement. Are devices clear?'):
        print('Cancelled. Hardware not opened.')
        return 0

    # On Windows, vendor/device adapter DLLs often search the working directory.
    os.environ['PATH'] = str(mm_dir) + os.pathsep + os.environ.get('PATH', '')
    if hasattr(os, 'add_dll_directory'):
        dll_search_handle = os.add_dll_directory(str(mm_dir))
        # Keep the handle alive until process exit.
    previous_dir = Path.cwd()
    core = None
    try:
        os.chdir(str(mm_dir))
        try:
            import pymmcore
        except ImportError as exc:
            print('Cannot import pymmcore:', exc)
            print('On Windows 7 use 64-bit Python 3.8, and an older compatible')
            print('pymmcore build that MATCHES your MM Device Interface API version.')
            return 2
        core = pymmcore.CMMCore()
        print('Python MMCore:', core.getVersionInfo())
        print('Python Device Interface:', core.getAPIVersionInfo())
        core.setDeviceAdapterSearchPaths([str(mm_dir)])
        try:
            core.loadSystemConfiguration(str(cfg))
        except Exception as exc:
            print('INITIALIZATION FAILED: {}: {}'.format(type(exc).__name__, exc))
            print('Check Micro-Manager GUI Help > About for its Device API version;')
            print('it must match the pymmcore Device API version above.')
            print('Also check vendor DLLs and ensure Micro-Manager Studio is closed.')
            return 3
        print('INITIALIZATION OK:', args.mode)
        get_status(core)
        console(core, can_write=args.enable_writes)
        return 0
    finally:
        if core is not None:
            try:
                core.reset()
                print('Devices unloaded.')
            except Exception as exc:
                print('WARNING: device cleanup failed:', exc)
        os.chdir(str(previous_dir))


if __name__ == '__main__':
    sys.exit(main())
