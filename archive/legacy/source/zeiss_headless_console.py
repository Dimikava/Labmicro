"""Read-only interactive Zeiss microscope controller using Micro-Manager Java Core.

Target: Windows 7 SP1 x64, Python 3.8, pycromanager 1.0.2, user's existing
Micro-Manager 2.0 installation. Requires the previously tested Python-3.8
compatibility patch for mmpycorex in this virtual environment.

The program NEVER sends set/move/home/shutter/lamp commands. Loading a hardware
configuration CAN still initialize devices and change their state.

Examples:
    python zeiss_headless_console.py
    python zeiss_headless_console.py --command status
    python zeiss_headless_console.py --config zeiss_hub_only.cfg
"""
import argparse
import gc
from pathlib import Path
import shlex
import sys
import traceback

Z_DEVICE = "ZeissFocusAxis"
XY_DEVICE = "ZeissXYStage"
STATE_DEVICES = (
    "ZeissObjectiveTurret",
    "ZeissReflectorTurret",
    "ZeissTubeLens",
    "ZeissSidePort",
    "ZeissBasePort",
    "ZeissExcitationSwitcher",
)

HELP = """
READ-ONLY COMMANDS
  help                       Show this help
  devices                    List loaded device labels
  info [device]              Show device identity / adapter
  status                     Read focus, XY, turret and port states
  z                          Read focus position in micrometers
  xy                         Read XY position in micrometers
  states                     Read discrete position states / labels
  properties DEVICE          List readable properties of one device
  property DEVICE NAME       Read one property (quote NAME if it has spaces)
  version                    Report the Micro-Manager Core version
  quit                       Stop the headless backend and exit

Examples:
  properties ZeissHalogenLamp
  properties ZeissScope
  property COM6 BaudRate
  property COM6 "Fast USB to Serial"

No motion, shutter or lamp commands are implemented.
""".strip()


def vector_to_list(vector):
    """MMCoreJ returns remote StrVector proxies, not Python iterables."""
    try:
        n = int(vector.size())
        return [str(vector.get(i)) for i in range(n)]
    except (AttributeError, TypeError):
        # Only for Java/Python bridge implementations that return a normal list.
        if isinstance(vector, (list, tuple)):
            return [str(item) for item in vector]
        raise


class ReadOnlyConsole:
    def __init__(self, core):
        self.core = core
        self.devices = vector_to_list(core.get_loaded_devices())

    def has(self, device):
        return device in self.devices

    @staticmethod
    def safe(label, callback):
        try:
            print("  {}: {}".format(label, callback()))
        except Exception as exc:
            print("  {}: unavailable ({})".format(label, exc))

    def show_devices(self):
        print("Loaded devices ({}):".format(len(self.devices)))
        for dev in self.devices:
            print("  " + dev)

    def show_info(self, device=None):
        names = [device] if device else self.devices
        for name in names:
            if not self.has(name):
                print("Unknown device: {}".format(name))
                continue
            print("{}".format(name))
            self.safe("Adapter", lambda n=name: self.core.get_device_library(n))
            self.safe("Native name", lambda n=name: self.core.get_device_name(n))
            self.safe("Type", lambda n=name: self.core.get_device_type(n))

    def show_z(self):
        if not self.has(Z_DEVICE):
            print("{} not loaded".format(Z_DEVICE))
            return
        self.safe("Z (um)", lambda: self.core.get_position(Z_DEVICE))

    def show_xy(self):
        if not self.has(XY_DEVICE):
            print("{} not loaded".format(XY_DEVICE))
            return
        self.safe("X (um)", lambda: self.core.get_x_position(XY_DEVICE))
        self.safe("Y (um)", lambda: self.core.get_y_position(XY_DEVICE))

    def show_states(self):
        for dev in STATE_DEVICES:
            if not self.has(dev):
                continue
            print(dev + ":")
            self.safe("State", lambda d=dev: self.core.get_state(d))
            self.safe("Label", lambda d=dev: self.core.get_state_label(d))

    def show_properties(self, device):
        if not self.has(device):
            print("Unknown device: {}".format(device))
            return
        try:
            names = vector_to_list(self.core.get_device_property_names(device))
        except Exception as exc:
            print("Cannot enumerate properties of {}: {}".format(device, exc))
            return
        print("{} readable properties of {}:".format(len(names), device))
        for name in names:
            self.safe(name, lambda n=name: self.core.get_property(device, n))

    def show_property(self, device, property_name):
        if not self.has(device):
            print("Unknown device: {}".format(device))
            return
        self.safe("{}.{}".format(device, property_name),
                  lambda: self.core.get_property(device, property_name))

    def show_status(self):
        print("Stage positions:")
        self.show_z()
        self.show_xy()
        print("Discrete device states:")
        self.show_states()
        print("Other components: 'properties ZeissHalogenLamp' for lamp details.")

    def dispatch(self, raw):
        try:
            args = shlex.split(raw)
        except ValueError as exc:
            print("Invalid quoting: {}".format(exc))
            return True
        if not args:
            return True
        command = args[0].lower()
        rest = args[1:]
        if command in ("quit", "exit", "q"):
            return False
        if command == "help":
            print(HELP)
        elif command == "devices":
            self.show_devices()
        elif command == "info":
            if len(rest) > 1:
                print("Usage: info [device]")
            else:
                self.show_info(rest[0] if rest else None)
        elif command == "status":
            self.show_status()
        elif command == "z":
            self.show_z()
        elif command == "xy":
            self.show_xy()
        elif command == "states":
            self.show_states()
        elif command == "properties":
            if len(rest) != 1:
                print("Usage: properties DEVICE")
            else:
                self.show_properties(rest[0])
        elif command == "property":
            if len(rest) < 2:
                print('Usage: property DEVICE "PROPERTY NAME"')
            else:
                self.show_property(rest[0], " ".join(rest[1:]))
        elif command == "version":
            self.safe("MMCore version", self.core.get_version_info)
        else:
            print("Unknown command: {}. Enter 'help'.".format(command))
        return True


def main():
    folder = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Read-only Zeiss headless interactive console")
    parser.add_argument("--mm-dir", default=r"C:\Program Files\Micro-Manager-2.0")
    parser.add_argument("--config", default=str(folder / "zeiss_only.cfg"))
    parser.add_argument("--command", help="Run one read-only command and exit")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    mm_dir = Path(args.mm_dir)
    cfg_file = Path(args.config)
    if not mm_dir.is_dir():
        print("ERROR: Micro-Manager folder not found: {}".format(mm_dir))
        return 2
    if not cfg_file.is_file():
        print("ERROR: Configuration not found: {}".format(cfg_file))
        return 2

    print("Micro-Manager: {}".format(mm_dir))
    print("Configuration: {}".format(cfg_file))
    print("CAUTION: Configuration initialization may change hardware state.")
    print("Close Micro-Manager GUI, LabVIEW, and any program using COM6.")
    try:
        answer = input("Type YES to initialize: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return 0
    if answer != "YES":
        print("Cancelled.")
        return 0

    try:
        from pycromanager import Core, start_headless, stop_headless
    except Exception:
        print("ERROR: Failed to import pycromanager:")
        traceback.print_exc()
        return 3

    started = False
    core = None
    console = None
    code = 0
    try:
        start_headless(mm_app_path=str(mm_dir), config_file=str(cfg_file),
                       python_backend=False, debug=args.debug)
        started = True
        core = Core()
        console = ReadOnlyConsole(core)
        print("\nHEADLESS INITIALIZATION OK. {} devices loaded.".format(len(console.devices)))
        console.show_devices()
        if args.command:
            console.dispatch(args.command)
        else:
            print("\nEnter 'help' for available commands; 'quit' to exit.")
            while True:
                try:
                    raw = input("zeiss> ")
                except (KeyboardInterrupt, EOFError):
                    print("\nExiting...")
                    break
                try:
                    if not console.dispatch(raw):
                        break
                except Exception:
                    print("Command failed; Java/Python traceback:")
                    traceback.print_exc()
    except Exception:
        print("INITIALIZATION FAILED:")
        traceback.print_exc()
        code = 1
    finally:
        console = None
        core = None
        gc.collect()
        if started:
            try:
                stop_headless()
                print("Headless backend stopped.")
            except Exception:
                print("WARNING: backend shutdown error:")
                traceback.print_exc()
                code = 1
        gc.collect()
    return code


if __name__ == "__main__":
    sys.exit(main())
