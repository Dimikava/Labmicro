"""Compatibility entrypoint for the consolidated v5 manual-control API."""
from pathlib import Path

from zeiss_v7.manual import *  # noqa: F401,F403
from zeiss_v7.manual import main as _main


def main(argv=None):
    return _main(argv, base_dir=Path(__file__).resolve().parent)


if __name__ == '__main__':
    import sys
    sys.exit(main())
