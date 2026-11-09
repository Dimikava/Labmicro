"""Read-only Zeiss hub check via Micro-Manager 2.0's Java backend.

For Windows 7 SP1 / Python 3.8. Uses your existing Micro-Manager
installation; does not need pymmcore or the Micro-Manager GUI.

Install: python -m pip install "pycromanager==1.0.2"
Run:     python headless_zeiss_java.py
Or:      python headless_zeiss_java.py --mm-dir "C:\\Program Files\\Micro-Manager-2.0"

Hardware initialization may change device state. Close Micro-Manager,
LabVIEW, and other applications using COM6 before running.
"""

import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Initialize ZeissScope headlessly using the existing Micro-Manager 2.0 Java core."
    )
    parser.add_argument(
        "--mm-dir",
        default=r"C:\Program Files\Micro-Manager-2.0",
        help="Directory containing the working Micro-Manager 2.0 installation",
    )
    parser.add_argument(
        "--config",
        default=str(Path(__file__).resolve().parent / "zeiss_hub_only.cfg"),
        help="Path to zeiss_hub_only.cfg",
    )
    args = parser.parse_args()

    mm_dir = Path(args.mm_dir)
    cfg_path = Path(args.config)
    if not mm_dir.is_dir():
        print("ERROR: Micro-Manager directory does not exist: {}".format(mm_dir))
        return 2
    if not cfg_path.is_file():
        print("ERROR: Configuration not found: {}".format(cfg_path))
        print("Place zeiss_hub_only.cfg beside this script, or pass --config PATH.")
        return 2

    print("Micro-Manager folder:", mm_dir)
    print("Configuration:", cfg_path)
    print("This will initialize microscope hardware using the Java MMCore.")
    print("Close the Micro-Manager GUI and any other program using COM6 first.")
    if input("Type YES to connect: ").strip() != "YES":
        print("Cancelled. No hardware connection attempted.")
        return 0

    try:
        from pycromanager import Core, start_headless, stop_headless
    except ImportError as exc:
        print("ERROR: Pycro-Manager is not installed in this environment:", exc)
        print('Install with: python -m pip install "pycromanager==1.0.2"')
        return 3

    started = False
    try:
        print("Starting Micro-Manager headless Java backend...")
        start_headless(
            mm_app_path=str(mm_dir),
            config_file=str(cfg_path),
            python_backend=False,
        )
        started = True
        core = Core()
        print("\nLoaded devices:")
        for device in core.get_loaded_devices():
            print(" -", device)
        print("\nSUCCESS: Java Micro-Manager Core connected.")
        print("No explicit movement or acquisition commands were sent.")
        return 0
    except Exception as exc:
        print("\nHEADLESS INITIALIZATION FAILED:")
        print("{}: {}".format(type(exc).__name__, exc))
        print("Check the install path, Java backend compatibility, and COM6 availability.")
        return 1
    finally:
        if started:
            try:
                stop_headless()
                print("Headless backend stopped.")
            except Exception as exc:
                print("WARNING: Could not stop headless backend cleanly:", exc)


if __name__ == "__main__":
    sys.exit(main())
