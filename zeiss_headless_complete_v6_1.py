"""Compatibility entrypoint for the consolidated v6.1 camera API."""
from pathlib import Path

from zeiss_v7.camera import *  # noqa: F401,F403
from zeiss_v7.camera import main as _main


def main(argv=None):
    return _main(argv, base_dir=Path(__file__).resolve().parent)


if __name__ == '__main__':
    import sys
    sys.exit(main())
