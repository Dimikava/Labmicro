"""Direct headless Zeiss control through the Micro-Manager 2.0 Java backend.

Target environment: Windows 7 SP1 x64, Python 3.8.10, pycromanager 1.0.2
with the previously verified mmpycorex Python-3.8 compatibility fix.

No custom software travel limits, arming flags or per-move confirmation.
Commands directly transmit requests to the device through MMCoreJ.
The device/firmware may still reject commands outside its physical limits.

No camera dependency: default config is zeiss_only.cfg alongside this script.
IMPORTANT: running the hardware configuration can itself change device states.
"""

import argparse
import gc
import math
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
QUERY
  help                           Show commands
  devices                        List loaded devices
  info [DEVICE]                  Show device adapter/type
  status                         Read Z, XY, discrete positions
  z                              Read focus Z (um)
  xy                             Read stage X,Y (um)
  states                         List current turret/port positions
  state DEVICE                   Read one state and label
  labels DEVICE                  List available state labels (indices start at 0)
  properties DEVICE              List device properties, values and read-only flags
  property DEVICE NAME           Read property (quote multiword names)
  shutter-status DEVICE          Read a shutter's state, if supported
  busy DEVICE                    Query whether a device is busy
  version                        Show core version

DIRECT WRITE (NO SOFTWARE LIMITS OR CONFIRMATION)
  move-z Z                       Absolute Z in um
  jog-z DZ                       Relative Z in um
  move-xy X Y                    Absolute XY in um
  jog-xy DX DY                   Relative XY in um
  set-state DEVICE INDEX         Change a turret/port by zero-based index
  set-label DEVICE LABEL         Change a turret/port using its configured label
  set-property DEVICE NAME VALUE Set any writable property
  shutter DEVICE open|close      Open/close a shutter, if supported
  stop DEVICE                    Ask a Z/XY stage to stop
  wait DEVICE                    Wait for device to report not busy
  quit                           Stop backend and exit

EXAMPLES
  jog-z 0.5
  move-xy 1000 2000
  labels ZeissObjectiveTurret
  set-state ZeissReflectorTurret 2
  set-label ZeissSidePort "1-100%L"
  properties ZeissHalogenLamp
  set-property ZeissHalogenLamp "<property from properties>" "<valid value>"

The PixeLINK camera is not included. Hardware motion is real and immediate.
""".strip()


def vector_to_list(vector):
    """Convert pyjavaz MMCoreJ StrVector/Structure, not directly iterable."""
    try:
        return [str(vector.get(i)) for i in range(int(vector.size()))]
    except (AttributeError, TypeError):
        if isinstance(vector, (tuple, list)):
            return [str(item) for item in vector]
        raise


def number(raw):
    """Require a finite numeric argument; impose no user-defined bounds."""
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("numeric argument must be finite")
    return value


def parse_open(raw):
    val = raw.strip().lower()
    if val in ("open", "on", "true", "1"):
        return True
    if val in ("close", "closed", "off", "false", "0"):
        return False
    raise ValueError("expected open or close")


class ZeissConsole:
    def __init__(self, core):
        self.core = core
        self.devices = vector_to_list(core.get_loaded_devices())

    def require(self, device):
        if device not in self.devices:
            raise ValueError("Device not loaded: {}".format(device))

    @staticmethod
    def show(label, fn):
        try:
            print("  {}: {}".format(label, fn()))
        except Exception as exc:
            print("  {}: unavailable ({})".format(label, exc))

    def show_devices(self):
        print("Loaded devices ({}):".format(len(self.devices)))
        for device in self.devices:
            print("  " + device)

    def show_info(self, device=None):
        for label in [device] if device else self.devices:
            self.require(label)
            print(label + ":")
            self.show("Library", lambda d=label: self.core.get_device_library(d))
            self.show("Adapter", lambda d=label: self.core.get_device_name(d))
            self.show("Type", lambda d=label: self.core.get_device_type(d))

    def show_z(self):
        self.require(Z_DEVICE)
        print("Z = {} um".format(self.core.get_position(Z_DEVICE)))

    def show_xy(self):
        self.require(XY_DEVICE)
        x = self.core.get_x_position(XY_DEVICE)
        y = self.core.get_y_position(XY_DEVICE)
        print("X = {} um; Y = {} um".format(x, y))

    def show_state(self, device):
        self.require(device)
        print("{}: state={} / label={}".format(
            device, self.core.get_state(device), self.core.get_state_label(device)))

    def show_states(self):
        for device in STATE_DEVICES:
            if device not in self.devices:
                continue
            try:
                self.show_state(device)
            except Exception as exc:
                print("{}: unavailable ({})".format(device, exc))

    def show_labels(self, device):
        self.require(device)
        labels = vector_to_list(self.core.get_state_labels(device))
        print("{} labels:".format(device))
        for idx, label in enumerate(labels):
            print("  {}: {}".format(idx, label))

    def show_properties(self, device):
        self.require(device)
        names = vector_to_list(self.core.get_device_property_names(device))
        print("Properties for {} ({}):".format(device, len(names)))
        for name in names:
            try:
                val = self.core.get_property(device, name)
            except Exception as exc:
                val = "<read error: {}>".format(exc)
            try:
                ro = " [READ ONLY]" if self.core.is_property_read_only(device, name) else ""
            except Exception:
                ro = ""
            print("  {} = {}{}".format(name, val, ro))

    def show_property(self, device, prop):
        self.require(device)
        print("{}.{} = {}".format(device, prop, self.core.get_property(device, prop)))
        try:
            allowed = vector_to_list(self.core.get_allowed_property_values(device, prop))
            if allowed:
                print("  allowed values: {}".format(", ".join(allowed)))
        except Exception:
            pass

    def show_status(self):
        for reader in (self.show_z, self.show_xy, self.show_states):
            try:
                reader()
            except Exception as exc:
                print("Status query unavailable: {}".format(exc))

    def _sent(self, text):
        # A successful RPC call is not proof that a motor reached its target.
        print("SENT: {}".format(text))

    def move_z(self, value):
        self.require(Z_DEVICE)
        self.core.set_position(Z_DEVICE, number(value))
        self._sent("absolute Z={} um".format(value))

    def jog_z(self, delta):
        self.require(Z_DEVICE)
        self.core.set_relative_position(Z_DEVICE, number(delta))
        self._sent("relative Z={} um".format(delta))

    def move_xy(self, x, y):
        self.require(XY_DEVICE)
        self.core.set_xy_position(XY_DEVICE, number(x), number(y))
        self._sent("absolute XY=({}, {}) um".format(x, y))

    def jog_xy(self, dx, dy):
        self.require(XY_DEVICE)
        self.core.set_relative_xy_position(XY_DEVICE, number(dx), number(dy))
        self._sent("relative XY=({}, {}) um".format(dx, dy))

    def set_state(self, device, idx):
        self.require(device)
        index = int(idx)
        self.core.set_state(device, index)
        self._sent("{} state {}".format(device, index))

    def set_label(self, device, label):
        self.require(device)
        self.core.set_state_label(device, label)
        self._sent("{} label {}".format(device, label))

    def set_property(self, device, name, val):
        self.require(device)
        # Send as string: MMCore parses the target device's property type.
        self.core.set_property(device, name, str(val))
        self._sent("{}.{} = {}".format(device, name, val))

    def shutter(self, device, opened):
        self.require(device)
        self.core.set_shutter_open(device, bool(opened))
        self._sent("{} shutter {}".format(device, "open" if opened else "close"))

    def dispatch(self, raw):
        try:
            words = shlex.split(raw)
        except ValueError as exc:
            print("Invalid quoting: {}".format(exc))
            return True
        if not words:
            return True
        cmd, args = words[0].lower(), words[1:]
        try:
            if cmd in ("exit", "quit", "q"):
                return False
            elif cmd == "help" and not args:
                print(HELP)
            elif cmd == "devices" and not args:
                self.show_devices()
            elif cmd == "info" and len(args) <= 1:
                self.show_info(args[0] if args else None)
            elif cmd == "status" and not args:
                self.show_status()
            elif cmd == "z" and not args:
                self.show_z()
            elif cmd == "xy" and not args:
                self.show_xy()
            elif cmd == "states" and not args:
                self.show_states()
            elif cmd == "state" and len(args) == 1:
                self.show_state(args[0])
            elif cmd == "labels" and len(args) == 1:
                self.show_labels(args[0])
            elif cmd == "properties" and len(args) == 1:
                self.show_properties(args[0])
            elif cmd == "property" and len(args) == 2:
                self.show_property(args[0], args[1])
            elif cmd == "shutter-status" and len(args) == 1:
                self.require(args[0])
                print("{} shutter open: {}".format(args[0], self.core.get_shutter_open(args[0])))
            elif cmd == "busy" and len(args) == 1:
                self.require(args[0])
                print("{} busy: {}".format(args[0], self.core.device_busy(args[0])))
            elif cmd == "version" and not args:
                print(self.core.get_version_info())
            elif cmd == "move-z" and len(args) == 1:
                self.move_z(args[0])
            elif cmd == "jog-z" and len(args) == 1:
                self.jog_z(args[0])
            elif cmd == "move-xy" and len(args) == 2:
                self.move_xy(*args)
            elif cmd == "jog-xy" and len(args) == 2:
                self.jog_xy(*args)
            elif cmd == "set-state" and len(args) == 2:
                self.set_state(args[0], args[1])
            elif cmd == "set-label" and len(args) == 2:
                self.set_label(args[0], args[1])
            elif cmd == "set-property" and len(args) == 3:
                self.set_property(*args)
            elif cmd == "shutter" and len(args) == 2:
                self.shutter(args[0], parse_open(args[1]))
            elif cmd == "stop" and len(args) == 1:
                self.require(args[0])
                self.core.stop(args[0])
                self._sent("stop {}".format(args[0]))
            elif cmd == "wait" and len(args) == 1:
                self.require(args[0])
                self.core.wait_for_device(args[0])
                print("{} is not busy".format(args[0]))
            else:
                print("Unknown command or incorrect arguments. Type 'help'.")
        except Exception as exc:
            # Device errors are reported; the console remains usable.
            print("COMMAND ERROR: {}".format(exc))
        return True


def main(argv=None):
    folder = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Direct Zeiss headless MMCoreJ control (no software motion guards)")
    parser.add_argument("--mm-dir", default=r"C:\Program Files\Micro-Manager-2.0")
    parser.add_argument("--config", default=str(folder / "zeiss_only.cfg"))
    parser.add_argument("--command", help="Execute one command, including write commands, and exit")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    mm_dir, config = Path(args.mm_dir), Path(args.config)
    if not mm_dir.is_dir():
        print("Micro-Manager folder missing: {}".format(mm_dir))
        return 2
    if not config.is_file():
        print("Config file missing: {}".format(config))
        return 2

    print("Micro-Manager: {}".format(mm_dir))
    print("Zeiss config: {}".format(config))
    print("DIRECT CONTROL: no per-command confirmations or software travel limits.")
    print("Close Micro-Manager GUI and LabVIEW before connecting to COM6.")

    try:
        from pycromanager import Core, start_headless, stop_headless
    except Exception:
        print("Pycro-Manager import failed:")
        traceback.print_exc()
        return 3

    started, core, console = False, None, None
    code = 0
    try:
        start_headless(mm_app_path=str(mm_dir), config_file=str(config),
                       python_backend=False, debug=args.debug)
        started = True
        core = Core()
        console = ZeissConsole(core)
        print("\nHEADLESS INITIALIZATION OK. {} devices loaded.".format(len(console.devices)))
        console.show_devices()
        if args.command:
            console.dispatch(args.command)
        else:
            print("\nType 'help' for commands; 'quit' to exit.")
            while True:
                try:
                    line = input("zeiss> ")
                except (EOFError, KeyboardInterrupt):
                    print("\nExiting...")
                    break
                if not console.dispatch(line):
                    break
    except Exception:
        print("HEADLESS BACKEND ERROR:")
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
                print("Shutdown warning:")
                traceback.print_exc()
                code = 1
        gc.collect()
    return code


if __name__ == "__main__":
    sys.exit(main())
