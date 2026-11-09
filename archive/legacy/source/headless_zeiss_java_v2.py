"""Read-only ZeissScope headless connection test using Micro-Manager's Java core.

Designed for the user's existing Micro-Manager 2.0 installation and Python 3.8
with pycromanager 1.0.2 (after the local mmpycorex Python 3.8 compatibility fix).

This version fixes the previous TypeError on mmcorej.StrVector: use .size()
and .get(index), not iteration over the Java proxy.

It only initializes the hub and reads device information; it does not
explicitly move a stage, switch a turret, or acquire images. Loading the
configuration may nevertheless initialize hardware and change its state.

Run with Micro-Manager GUI / LabVIEW closed, no other owner of COM6:
    python headless_zeiss_java_v2.py
"""

import argparse
import gc
from pathlib import Path
import sys
import traceback


def java_str_vector_to_list(vector):
    """Convert a Pycro-Manager Java mmcorej.StrVector proxy to Python strings."""
    return [str(vector.get(i)) for i in range(int(vector.size()))]


def main():
    parser = argparse.ArgumentParser(description="Read-only Zeiss Java-headless hub test")
    parser.add_argument(
        "--mm-dir",
        default=r"C:\Program Files\Micro-Manager-2.0",
        help="Path to existing Micro-Manager 2.0 folder",
    )
    parser.add_argument(
        "--config",
        default=str(Path(__file__).resolve().parent / "zeiss_only.cfg"),
        help="Hub-only Zeiss config path",
    )
    parser.add_argument("--debug", action="store_true", help="Print Java backend debug output")
    args = parser.parse_args()

    mm_dir = Path(args.mm_dir).resolve()
    cfg_path = Path(args.config).resolve()
    if not mm_dir.is_dir():
        print("ERROR: Micro-Manager installation directory does not exist:", mm_dir)
        return 2
    if not cfg_path.is_file():
        print("ERROR: Config does not exist:", cfg_path)
        return 2

    print("Micro-Manager folder:", mm_dir)
    print("Configuration:", cfg_path)
    print("WARNING: Loading this config initializes microscope hardware.")
    print("Close Micro-Manager GUI, LabVIEW, and anything using COM6 first.")
    if input("Type YES to connect: ").strip() != "YES":
        print("Cancelled; no hardware connection attempted.")
        return 0

    try:
        from pycromanager import Core, start_headless, stop_headless
    except Exception:
        print("ERROR: Could not import pycromanager; import traceback follows:")
        traceback.print_exc()
        return 3

    started = False
    core = None
    raw_devices = None
    result = 1
    try:
        print("Starting Micro-Manager Java headless backend...")
        start_headless(
            mm_app_path=str(mm_dir),
            config_file=str(cfg_path),
            python_backend=False,
            debug=args.debug,
        )
        started = True
        core = Core()

        # Pycro-Manager returns a remote Java StrVector, not a Python iterable.
        raw_devices = core.get_loaded_devices()
        device_names = java_str_vector_to_list(raw_devices)
        del raw_devices
        raw_devices = None

        print("\nLoaded devices ({}):".format(len(device_names)))
        for name in device_names:
            print(" -", name)

        if "ZeissScope" not in device_names:
            print("\nWARNING: ZeissScope is NOT listed among loaded devices.")
            print("Check the configuration and the Java/Core logs.")
            result = 1
        else:
            print("\nZeissScope appears among loaded devices.")
            # Device property reads do not explicitly command movement.
            for dev, prop in [("ZeissScope", "Port"), ("COM6", "BaudRate")]:
                if dev in device_names:
                    try:
                        print("{}.{} = {}".format(dev, prop, core.get_property(dev, prop)))
                    except Exception as exc:
                        print("Could not read {}.{}: {}".format(dev, prop, exc))
            print("\nHEADLESS CORE QUERY SUCCESSFUL. No explicit movement commands sent.")
            result = 0
    except Exception:
        print("\nHEADLESS TEST FAILED; complete traceback:")
        traceback.print_exc()
        result = 1
    finally:
        # Release remote Java proxies while the bridge still exists. This can
        # reduce pyjavaz destructor warnings when the Python process exits.
        raw_devices = None
        core = None
        gc.collect()
        if started:
            try:
                stop_headless()
                print("Headless backend stopped.")
            except Exception:
                print("WARNING: Headless backend cleanup raised an error:")
                traceback.print_exc()
                result = 1
        gc.collect()
    return result


if __name__ == "__main__":
    sys.exit(main())
