"""Conservative headless Zeiss controller for Micro-Manager 2.0 Java backend.

Target: Windows 7 SP1 x64, Python 3.8, pycromanager 1.0.2.
Prerequisite: the user's already tested Pycro-Manager Python-3.8 patch.

No camera dependency. Uses zeiss_only.cfg in the same folder by default.
Read-only by default. Stage moves require --enable-motion, explicit stage
limits, small per-command displacement and a typed confirmation per move.

WARNING: Software checks do NOT establish mechanical safety or collision
clearance. Movement and even device initialization can alter hardware state.
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
READ-ONLY COMMANDS
  help                      List commands
  devices                   List loaded devices
  info [DEVICE]             Describe a device (or all devices)
  status                    Read stage positions and discrete states
  z                         Read Z/focus position, micrometers
  xy                        Read XY stage position, micrometers
  states                    Read turret and optical port states
  properties DEVICE         List a device's properties
  property DEVICE NAME      Read one property (quote names with spaces)
  version                   Show Micro-Manager Core version
  limits                    Display software motion restrictions
  quit                      Close backend and exit

MOTION COMMANDS — DISABLED UNLESS EXPLICITLY ARMED AT STARTUP
  move-z Z                  Absolute Z target in micrometers
  jog-z DZ                  Relative Z step in micrometers
  move-xy X Y               Absolute XY target in micrometers
  jog-xy DX DY              Relative XY step in micrometers

Motion requires --enable-motion and explicit --z-limits and/or --xy-limits.
Each move reads the current position, checks bounds and maximum step size,
then requires typing MOVE before a command is sent.

No turret, shutter, lamp or other property-writing commands are included.
Software limits cannot protect against sample/objective collisions.
""".strip()


def vector_to_list(vector):
    """MMCoreJ StrVector proxies are not Python iterables."""
    try:
        return [str(vector.get(i)) for i in range(int(vector.size()))]
    except (AttributeError, TypeError):
        if isinstance(vector, (tuple, list)):
            return [str(x) for x in vector]
        raise


def finite_number(raw):
    try:
        number = float(raw)
    except (ValueError, TypeError):
        raise ValueError("Expected a numerical position in micrometers")
    if not math.isfinite(number):
        raise ValueError("NaN or infinity is not allowed")
    return number


def in_bounds(value, low, high):
    return math.isfinite(value) and low <= value <= high


class ZeissConsole:
    def __init__(self, core, enable_motion=False, z_limits=None, xy_limits=None,
                 max_z_step=1.0, max_xy_step=10.0, input_fn=input):
        self.core = core
        self.devices = vector_to_list(core.get_loaded_devices())
        self.enable_motion = bool(enable_motion)
        self.z_limits = z_limits
        self.xy_limits = xy_limits
        self.max_z_step = float(max_z_step)
        self.max_xy_step = float(max_xy_step)
        self.input_fn = input_fn

    def has(self, name):
        return name in self.devices

    @staticmethod
    def safe(label, callback):
        try:
            print("  {}: {}".format(label, callback()))
        except Exception as exc:
            print("  {}: unavailable ({})".format(label, exc))

    def show_devices(self):
        print("Loaded devices ({}):".format(len(self.devices)))
        for name in self.devices:
            print("  " + name)

    def show_info(self, name=None):
        for device in ([name] if name else self.devices):
            if not self.has(device):
                print("Unknown device: {}".format(device))
                continue
            print("{}:".format(device))
            self.safe("Adapter", lambda d=device: self.core.get_device_library(d))
            self.safe("Native name", lambda d=device: self.core.get_device_name(d))
            self.safe("Type", lambda d=device: self.core.get_device_type(d))

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
        for device in STATE_DEVICES:
            if not self.has(device):
                continue
            print("{}:".format(device))
            self.safe("State", lambda d=device: self.core.get_state(d))
            self.safe("Label", lambda d=device: self.core.get_state_label(d))

    def show_properties(self, device):
        if not self.has(device):
            print("Unknown device: {}".format(device))
            return
        try:
            names = vector_to_list(self.core.get_device_property_names(device))
        except Exception as exc:
            print("Cannot enumerate properties: {}".format(exc))
            return
        print("{} properties for {}:".format(len(names), device))
        for name in names:
            self.safe(name, lambda n=name: self.core.get_property(device, n))

    def show_property(self, device, prop):
        if not self.has(device):
            print("Unknown device: {}".format(device))
            return
        self.safe("{}.{}".format(device, prop),
                  lambda: self.core.get_property(device, prop))

    def show_status(self):
        print("Stage positions:")
        self.show_z()
        self.show_xy()
        print("Discrete states:")
        self.show_states()

    def show_limits(self):
        print("Motion armed: {}".format(self.enable_motion))
        print("Z limits (um): {}".format(self.z_limits))
        print("XY limits (um): {}".format(self.xy_limits))
        print("Max Z move per command (um): {}".format(self.max_z_step))
        print("Max XY displacement per command (um): {}".format(self.max_xy_step))
        if not self.enable_motion:
            print("READ-ONLY: use --enable-motion to permit movement.")
        print("Bounds are USER-SUPPLIED software checks, not hardware limit switches.")

    def _confirm(self, detail):
        print("\nPROPOSED HARDWARE MOVEMENT: " + detail)
        print("Check objective/sample clearance, cables, stage direction and interlocks.")
        try:
            answer = self.input_fn("Type MOVE to transmit the command: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled; no motion command sent.")
            return False
        if answer != "MOVE":
            print("Cancelled; no motion command sent.")
            return False
        return True

    def _permit(self, device, bounds):
        if not self.enable_motion:
            print("BLOCKED: read-only mode. Restart with --enable-motion and explicit limits.")
            return False
        if not self.has(device):
            print("BLOCKED: stage {} is not loaded.".format(device))
            return False
        if bounds is None:
            print("BLOCKED: limits for {} were not supplied at startup.".format(device))
            return False
        return True

    def move_z(self, target=None, delta=None):
        if not self._permit(Z_DEVICE, self.z_limits):
            return
        try:
            current = finite_number(self.core.get_position(Z_DEVICE))
            target = finite_number(current + delta if delta is not None else target)
            low, high = self.z_limits
            if not in_bounds(current, low, high):
                print("BLOCKED: actual Z {:.5f} um is outside supplied limits.".format(current))
                return
            if not in_bounds(target, low, high):
                print("BLOCKED: target Z {:.5f} um is outside supplied limits.".format(target))
                return
            distance = abs(target - current)
            if distance > self.max_z_step + 1e-9:
                print("BLOCKED: Z displacement {:.5f} um exceeds {:.5f} um per command."
                      .format(distance, self.max_z_step))
                return
            if distance < 1e-9:
                print("No movement needed.")
                return
            if self._confirm("Z {:.5f} -> {:.5f} um (delta {:+.5f} um)"
                             .format(current, target, target - current)):
                self.core.set_position(Z_DEVICE, target)
                print("Movement command submitted. Use 'z' to verify position.")
        except Exception as exc:
            print("No confirmed successful movement; error: {}".format(exc))

    def move_xy(self, target_x=None, target_y=None, delta_x=None, delta_y=None):
        if not self._permit(XY_DEVICE, self.xy_limits):
            return
        try:
            x = finite_number(self.core.get_x_position(XY_DEVICE))
            y = finite_number(self.core.get_y_position(XY_DEVICE))
            tx = finite_number(x + delta_x if delta_x is not None else target_x)
            ty = finite_number(y + delta_y if delta_y is not None else target_y)
            xmin, xmax, ymin, ymax = self.xy_limits
            if not in_bounds(x, xmin, xmax) or not in_bounds(y, ymin, ymax):
                print("BLOCKED: actual XY ({:.5f}, {:.5f}) um is outside supplied limits."
                      .format(x, y))
                return
            if not in_bounds(tx, xmin, xmax) or not in_bounds(ty, ymin, ymax):
                print("BLOCKED: target XY ({:.5f}, {:.5f}) um is outside supplied limits."
                      .format(tx, ty))
                return
            distance = math.hypot(tx - x, ty - y)
            if distance > self.max_xy_step + 1e-9:
                print("BLOCKED: XY displacement {:.5f} um exceeds {:.5f} um per command."
                      .format(distance, self.max_xy_step))
                return
            if distance < 1e-9:
                print("No movement needed.")
                return
            if self._confirm("XY ({:.5f}, {:.5f}) -> ({:.5f}, {:.5f}) um (distance {:.5f} um)"
                             .format(x, y, tx, ty, distance)):
                self.core.set_xy_position(XY_DEVICE, tx, ty)
                print("Movement command submitted. Use 'xy' to verify position.")
        except Exception as exc:
            print("No confirmed successful movement; error: {}".format(exc))

    def dispatch(self, raw):
        try:
            parts = shlex.split(raw)
        except ValueError as exc:
            print("Invalid quoting: {}".format(exc))
            return True
        if not parts:
            return True
        command, rest = parts[0].lower(), parts[1:]
        if command in ("q", "quit", "exit"):
            return False
        if command == "help":
            print(HELP)
        elif command == "devices":
            self.show_devices()
        elif command == "info" and len(rest) <= 1:
            self.show_info(rest[0] if rest else None)
        elif command == "status":
            self.show_status()
        elif command == "z":
            self.show_z()
        elif command == "xy":
            self.show_xy()
        elif command == "states":
            self.show_states()
        elif command == "properties" and len(rest) == 1:
            self.show_properties(rest[0])
        elif command == "property" and len(rest) >= 2:
            self.show_property(rest[0], " ".join(rest[1:]))
        elif command == "version":
            self.safe("MMCore version", self.core.get_version_info)
        elif command == "limits":
            self.show_limits()
        elif command in ("move-z", "jog-z", "move-xy", "jog-xy"):
            expected = 1 if command.endswith("z") else 2
            if len(rest) != expected:
                print("Expected {} numeric argument(s): {}".format(expected, command))
                return True
            try:
                values = [finite_number(x) for x in rest]
            except ValueError as exc:
                print("Invalid movement argument: {}".format(exc))
                return True
            if command == "move-z":
                self.move_z(target=values[0])
            elif command == "jog-z":
                self.move_z(delta=values[0])
            elif command == "move-xy":
                self.move_xy(target_x=values[0], target_y=values[1])
            elif command == "jog-xy":
                self.move_xy(delta_x=values[0], delta_y=values[1])
        else:
            print("Unknown command or invalid arguments. Enter 'help'.")
        return True


def validated_bounds(items, name):
    if items is None:
        return None
    values = tuple(finite_number(x) for x in items)
    if name == "Z":
        if values[0] >= values[1]:
            raise ValueError("--z-limits requires MIN < MAX")
    else:
        if values[0] >= values[1] or values[2] >= values[3]:
            raise ValueError("--xy-limits requires XMIN < XMAX and YMIN < YMAX")
    return values


def main(argv=None):
    folder = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Zeiss Java headless console (read-only by default)")
    parser.add_argument("--mm-dir", default=r"C:\Program Files\Micro-Manager-2.0")
    parser.add_argument("--config", default=str(folder / "zeiss_only.cfg"))
    parser.add_argument("--command", help="One-shot READ-ONLY command")
    parser.add_argument("--enable-motion", action="store_true", help="Arm explicit stage movements")
    parser.add_argument("--z-limits", nargs=2, type=float, metavar=("MIN", "MAX"))
    parser.add_argument("--xy-limits", nargs=4, type=float,
                        metavar=("XMIN", "XMAX", "YMIN", "YMAX"))
    parser.add_argument("--max-z-step", type=float, default=1.0)
    parser.add_argument("--max-xy-step", type=float, default=10.0)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    try:
        z_limits = validated_bounds(args.z_limits, "Z")
        xy_limits = validated_bounds(args.xy_limits, "XY")
        if (not math.isfinite(args.max_z_step) or args.max_z_step <= 0 or
                not math.isfinite(args.max_xy_step) or args.max_xy_step <= 0):
            raise ValueError("Maximum move distances must be finite and positive")
    except ValueError as exc:
        parser.error(str(exc))
    if args.enable_motion and z_limits is None and xy_limits is None:
        parser.error("--enable-motion requires --z-limits and/or --xy-limits")
    if args.command:
        try:
            action = shlex.split(args.command)[0].lower()
        except (ValueError, IndexError):
            action = ""
        if action in ("move-z", "jog-z", "move-xy", "jog-xy"):
            parser.error("One-shot motion is disabled; use the interactive console")

    mm_dir = Path(args.mm_dir)
    config = Path(args.config)
    if not mm_dir.is_dir() or not config.is_file():
        print("ERROR: Missing directory or file: MM={} / config={}".format(mm_dir, config))
        return 2

    print("Micro-Manager: {}".format(mm_dir))
    print("Configuration: {}".format(config))
    print("Stage motion: {}".format("ARMED" if args.enable_motion else "DISABLED (read-only)"))
    if args.enable_motion:
        print("Z limits: {} / XY limits: {} (micrometers)".format(z_limits, xy_limits))
    print("CAUTION: configuration initialization can still change device states.")
    print("Close Micro-Manager GUI, LabVIEW and any other COM6 clients first.")
    try:
        if input("Type YES to initialize: ").strip() != "YES":
            print("Cancelled.")
            return 0
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return 0

    try:
        from pycromanager import Core, start_headless, stop_headless
    except Exception:
        print("ERROR: importing pycromanager failed:")
        traceback.print_exc()
        return 3

    started, core, console, exit_code = False, None, None, 0
    try:
        start_headless(mm_app_path=str(mm_dir), config_file=str(config),
                       python_backend=False, debug=args.debug)
        started = True
        core = Core()
        console = ZeissConsole(core, enable_motion=args.enable_motion,
                               z_limits=z_limits, xy_limits=xy_limits,
                               max_z_step=args.max_z_step, max_xy_step=args.max_xy_step)
        print("\nHEADLESS CORE INITIALIZED. {} devices loaded.".format(len(console.devices)))
        console.show_devices()
        if args.command:
            console.dispatch(args.command)
        else:
            print("\nEnter 'help' for commands. Type 'quit' to exit.")
            while True:
                try:
                    raw = input("zeiss> ")
                except (EOFError, KeyboardInterrupt):
                    print("\nExiting...")
                    break
                try:
                    if not console.dispatch(raw):
                        break
                except Exception:
                    print("Command exception:")
                    traceback.print_exc()
    except Exception:
        print("INITIALIZATION FAILED:")
        traceback.print_exc()
        exit_code = 1
    finally:
        console = None
        core = None
        gc.collect()
        if started:
            try:
                stop_headless()
                print("Backend stopped.")
            except Exception:
                print("WARNING: backend shutdown error:")
                traceback.print_exc()
                exit_code = 1
        gc.collect()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
