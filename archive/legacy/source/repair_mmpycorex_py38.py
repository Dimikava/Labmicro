"""Reversible Python 3.8 compatibility patch for mmpycorex 0.3.16.

Only edits the selected Python environment's mmpycorex/launcher.py.
Does not connect to the microscope, install packages, or modify Micro-Manager.

Usage (inside activated mm38):
    python repair_mmpycorex_py38.py --check
    python repair_mmpycorex_py38.py --apply
    python repair_mmpycorex_py38.py --restore
"""

import argparse
import shutil
import sys
from pathlib import Path

SIGNATURE = '_JAVA_HEADLESS_SUBPROCESSES : dict[int, subprocess.Popen] = {}'
PATCH_LINE = 'from __future__ import annotations\n'
BACKUP_SUFFIX = '.before_py38_patch.bak'


def get_target():
    # Resolves the package in the *active* interpreter's environment.
    package = Path(sys.prefix) / 'Lib' / 'site-packages' / 'mmpycorex'
    return package / 'launcher.py'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true', help='Read-only checks')
    group.add_argument('--apply', action='store_true', help='Back up and patch launcher.py')
    group.add_argument('--restore', action='store_true', help='Restore the original launcher.py')
    args = p.parse_args()

    print('Python:', sys.version.split()[0])
    print('Environment:', sys.prefix)
    if sys.prefix == sys.base_prefix:
        print('REFUSING: no active virtual environment. Activate mm38 first.')
        return 2
    if sys.version_info[:2] != (3, 8):
        print('REFUSING: designed for Python 3.8 only.')
        return 2

    target = get_target()
    backup = Path(str(target) + BACKUP_SUFFIX)
    print('Target:', target)
    print('Backup:', backup)
    if not target.is_file():
        print('ERROR: mmpycorex launcher.py not found at this path.')
        return 2

    source = target.read_text(encoding='utf-8-sig')
    patched = source.startswith(PATCH_LINE)
    print('Compatibility import already applied:', patched)
    print('Expected incompatible annotation found:', SIGNATURE in source)
    print('Backup exists:', backup.exists())
    if args.check:
        print('Read-only check complete.')
        return 0

    if args.apply:
        if patched:
            print('Already patched. No files changed.')
            return 0
        if SIGNATURE not in source:
            print('REFUSING: expected source signature not found.')
            return 2
        if backup.exists():
            print('REFUSING: a backup already exists; investigate before overwriting.')
            return 2
        shutil.copy2(str(target), str(backup))
        new_source = PATCH_LINE + source
        # A syntax check does not execute the package or communicate with hardware.
        compile(new_source, str(target), 'exec')
        try:
            target.write_text(new_source, encoding='utf-8')
        except Exception:
            shutil.copy2(str(backup), str(target))
            raise
        print('Applied: postponed type-annotation evaluation in launcher.py.')
        print('Original preserved in:', backup)
        print('Now run: python -c "import pycromanager; print(\'IMPORT OK\')"')
        return 0

    if args.restore:
        if not backup.exists():
            print('REFUSING: original backup is not present.')
            return 2
        if not patched:
            print('REFUSING: installed source is not the expected patched form.')
            return 2
        original = backup.read_text(encoding='utf-8-sig')
        if source != PATCH_LINE + original:
            print('REFUSING: source has been changed since patching.')
            return 2
        shutil.copy2(str(backup), str(target))
        print('Original launcher.py restored. Backup remains available.')
        return 0

    return 2


if __name__ == '__main__':
    sys.exit(main())
