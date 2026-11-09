"""Stable launcher for the consolidated Zeiss + PixeLINK v7 controller."""
from zeiss_v7.app import *  # noqa: F401,F403


if __name__ == '__main__':
    import sys
    sys.exit(main())
