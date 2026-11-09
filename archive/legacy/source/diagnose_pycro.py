"""Read-only compatibility diagnostic for Pycro-Manager on a Windows 7 Micro-Manager lab PC.

Run in the mm38 virtual environment:
    python diagnose_pycro.py

No microscope connection is attempted. No hardware is initialized or moved.
The script writes pycro_diagnostic.txt alongside itself.
"""
from __future__ import print_function

import importlib
import os
from pathlib import Path
import platform
import subprocess
import sys
import traceback


HERE = Path(__file__).resolve().parent
REPORT = HERE / "pycro_diagnostic.txt"
MM_DIR = Path(r"C:\Program Files\Micro-Manager-2.0")


def main():
    lines = []

    def log(value=""):
        value = str(value)
        print(value)
        lines.append(value)

    log("=== Pycro-Manager diagnostic (read-only) ===")
    log("Python executable: {}".format(sys.executable))
    log("Python version: {}".format(sys.version.replace("\n", " ")))
    log("Architecture: {} / {}".format(platform.system(), platform.architecture()[0]))
    log("Working directory: {}".format(os.getcwd()))
    log("Micro-Manager directory exists: {} ({})".format(MM_DIR.is_dir(), MM_DIR))
    if MM_DIR.is_dir():
        for name in ("MMCoreJ.jar", "mmgr_dal_ZeissCAN29.dll", "mmgr_dal_SerialManager.dll"):
            log("  {}: {}".format(name, (MM_DIR / name).exists()))

    log("\n=== Installed Python packages ===")
    try:
        from importlib import metadata
    except ImportError:
        metadata = None
    if metadata is not None:
        for package in ("pycromanager", "mmpycorex", "pyjavaz", "pyzmq", "numpy", "dask", "ndtiff", "pymmcore", "setuptools", "pip"):
            try:
                log("{}: {}".format(package, metadata.version(package)))
            except metadata.PackageNotFoundError:
                log("{}: NOT INSTALLED".format(package))

    log("\n=== Dependency consistency (pip check) ===")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "check"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, timeout=30,
        )
        log(result.stdout.strip() or "(no output)")
        log("pip check exit code: {}".format(result.returncode))
    except Exception:
        log(traceback.format_exc())

    log("\n=== Import test (NO hardware connection) ===")
    try:
        module = importlib.import_module("pycromanager")
        log("pycromanager import: OK")
        log("Package path: {}".format(getattr(module, "__file__", "unknown")))
        for name in ("Core", "start_headless", "stop_headless"):
            log("{} available: {}".format(name, hasattr(module, name)))
    except BaseException:
        log("pycromanager import: FAILED")
        log(traceback.format_exc())

    try:
        REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\nReport saved to: {}".format(REPORT))
    except Exception as exc:
        print("Could not save report: {}".format(exc))
        print("Copy the console output instead.")


if __name__ == "__main__":
    main()
