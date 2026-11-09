"""Headless Zeiss Axio Observer Z1 controller, using Micro-Manager 2.0's Java Core.

Target: Windows 7 SP1 x64, Python 3.8.10, pycromanager 1.0.2 and the
already working Java-headless setup. Uses zeiss_only.cfg (no PixeLINK camera).

This controller does NOT add software travel limits, confirmations, or arm flags.
Commands go straight to the Micro-Manager Core. Loading the config may itself
change hardware state. Firmware/hardware controls remain in force.

The ZeissHardware class can also be imported into another Python application.
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
LAMP_DEVICE = "ZeissHalogenLamp"

ALIASES = {
    "objective": "ZeissObjectiveTurret",
    "reflector": "ZeissReflectorTurret",
    "tube": "ZeissTubeLens",
    "side": "ZeissSidePort",
    "base": "ZeissBasePort",
    "excitation": "ZeissExcitationSwitcher",
    "focus": Z_DEVICE,
    "stage": XY_DEVICE,
    "lamp": LAMP_DEVICE,
    "scope": "ZeissScope",
    "serial": "COM6",
}
STATE_ALIASES = ("objective", "reflector", "tube", "side", "base", "excitation")

HELP = """
READ / DISCOVER
  help                                 Show this help
  devices                              List loaded devices and types
  available [LIBRARY]                  Show available adapter types (default ZeissCAN29)
  info [DEVICE]                        Adapter, type, description
  status                               Z, XY, turret/port positions, lamp properties
  all                                  Properties of all loaded devices
  z                                    Current Z position in um
  xy                                   Current XY position in um
  states                               Current states of configured turrets and ports
  state DEVICE                         Read state index and label
  labels DEVICE                        List 0-based state labels
  properties DEVICE                    All property values; read-only/allowed/ranges
  property DEVICE NAME                 Read one property and its metadata
  busy DEVICE                          Busy flag
  roles                                Current MMCore focus, XY, shutter, camera roles
  shutter-status [DEVICE]              Shutter state (default if omitted)
  autoshutter                          Automatic shutter setting
  configs [GROUP]                      Config groups, or presets in a group
  version                              MMCore version and device API

DIRECT CONTROL (NO SOFTWARE TRAVEL LIMITS OR CONFIRMATIONS)
  move-z UM                            Absolute focus position
  jog-z DELTA                          Relative focus position
  move-xy X Y                          Absolute XY position
  jog-xy DX DY                         Relative XY position
  jog-x DX                             Relative X-only movement
  jog-y DY                             Relative Y-only movement
  home DEVICE                          Hardware homing, if supported
  zero-z                               Zero focus coordinate at current position
  zero-xy                              Zero XY coordinates at current position
  stop DEVICE                          Stop focus or XY stage
  wait DEVICE                          Wait until device is no longer busy
  set-state DEVICE INDEX               Change state by zero-based index
  set-label DEVICE LABEL               Change state by configured label
  set-property DEVICE NAME VALUE       Set any writable property
  shutter [DEVICE] open|close          Set default or named shutter
  autoshutter on|off                   Set auto-shutter behavior
  preset GROUP NAME                    Apply a defined configuration preset
  timeout MS                           Set MMCore wait timeout

SHORTCUTS FOR EVERY CONFIGURED STATE DEVICE
  objective [INDEX|LABEL]              Objective turret read or set
  reflector [INDEX|LABEL]              Reflector turret read or set
  tube [INDEX|LABEL]                   Tube lens read or set
  side [INDEX|LABEL]                   Side optical port read or set
  base [INDEX|LABEL]                   Base optical port read or set
  excitation [INDEX|LABEL]             Excitation switcher read or set

LAMP / GENERIC CORE ACCESS
  lamp                                 Display all halogen lamp properties
  lamp NAME VALUE                      Write halogen lamp property
  core METHOD [ARGS...]                Call any MMCore method by snake_case name
      Core-call arguments are strings by default. Use prefixes:
      int:3   float:2.5   bool:true   str:literal
      Example: core get_position ZeissFocusAxis
      Example: core set_position ZeissFocusAxis float:100.0

OTHER
  quit                                 Exit controller
  --command "..."                      Execute one command, then exit
  --commands-file FILE                 Execute text-file commands, then exit

DEVICE may be a full label or a shortcut: objective, reflector, tube,
side, base, excitation, focus, stage, lamp, scope, serial.
Quote names with spaces: lamp "<real property name>" "<real value>".
Example: side "1-100%L"; objective 1; jog-z 0.5; jog-xy 5 -2.
The PixeLINK camera and devices absent from zeiss_only.cfg are NOT loaded.
""".strip()


def vec(value):
    """Convert MMCoreJ StrVector / Java Structure, which may not be iterable."""
    if hasattr(value, "size") and hasattr(value, "get"):
        return [str(value.get(i)) for i in range(int(value.size()))]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    raise TypeError("Unexpected MMCore vector: {}".format(type(value)))


def num(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Expected finite number")
    return value


def switch(value):
    s = str(value).lower()
    if s in ("on", "open", "true", "1", "yes"):
        return True
    if s in ("off", "close", "closed", "false", "0", "no"):
        return False
    raise ValueError("Expected on/off or open/close")


def core_arg(token):
    """Explicitly typed args for the unrestricted generic Java Core interface."""
    if token.startswith("int:"):
        return int(token[4:])
    if token.startswith("float:"):
        return num(token[6:])
    if token.startswith("bool:"):
        return switch(token[5:])
    if token.startswith("str:"):
        return token[4:]
    return token


class ZeissHardware:
    """Thin reusable wrapper around a successfully initialized remote MMCoreJ."""

    def __init__(self, core):
        self.core = core
        self.devices = vec(core.get_loaded_devices())

    def device(self, label):
        name = ALIASES.get(label.lower(), label)
        if name not in self.devices:
            raise ValueError("Not loaded: {}. Run 'devices' to inspect.".format(name))
        return name

    def get_z(self):
        return float(self.core.get_position(self.device(Z_DEVICE)))

    def get_xy(self):
        dev = self.device(XY_DEVICE)
        return (float(self.core.get_x_position(dev)),
                float(self.core.get_y_position(dev)))

    def move_z(self, value):
        self.core.set_position(self.device(Z_DEVICE), num(value))

    def jog_z(self, delta):
        self.core.set_relative_position(self.device(Z_DEVICE), num(delta))

    def move_xy(self, x, y):
        self.core.set_xy_position(self.device(XY_DEVICE), num(x), num(y))

    def jog_xy(self, dx, dy):
        self.core.set_relative_xy_position(self.device(XY_DEVICE), num(dx), num(dy))

    def get_state(self, label):
        dev = self.device(label)
        return int(self.core.get_state(dev)), str(self.core.get_state_label(dev))

    def labels(self, label):
        return vec(self.core.get_state_labels(self.device(label)))

    def set_state(self, label, index):
        self.core.set_state(self.device(label), int(index))

    def set_label(self, label, value):
        self.core.set_state_label(self.device(label), str(value))

    def set_property(self, label, name, value):
        self.core.set_property(self.device(label), name, str(value))

    def get_property(self, label, name):
        return str(self.core.get_property(self.device(label), name))

    def property_names(self, label):
        return vec(self.core.get_device_property_names(self.device(label)))

    def set_shutter(self, opened, label=None):
        if label is None:
            self.core.set_shutter_open(bool(opened))
        else:
            self.core.set_shutter_open(self.device(label), bool(opened))

    def get_shutter(self, label=None):
        if label is None:
            return bool(self.core.get_shutter_open())
        return bool(self.core.get_shutter_open(self.device(label)))

    def stop(self, label):
        self.core.stop(self.device(label))

    def wait(self, label):
        self.core.wait_for_device(self.device(label))

    def home(self, label):
        self.core.home(self.device(label))

    def zero_z(self):
        self.core.set_origin(self.device(Z_DEVICE))

    def zero_xy(self):
        self.core.set_origin_xy(self.device(XY_DEVICE))

    def call_core(self, method, arguments):
        fn = getattr(self.core, method)
        if not callable(fn):
            raise TypeError("Core attribute is not callable: {}".format(method))
        return fn(*[core_arg(arg) for arg in arguments])


class ZeissConsole:
    def __init__(self, core):
        self.hw = ZeissHardware(core)
        self.core = core
        self.last_error = None

    @staticmethod
    def sent(message):
        print("SENT: {}".format(message))

    @staticmethod
    def safe(label, fn):
        try:
            print("  {}: {}".format(label, fn()))
        except Exception as exc:
            print("  {}: unavailable ({})".format(label, exc))

    def show_devices(self):
        print("Loaded devices ({}):".format(len(self.hw.devices)))
        for d in self.hw.devices:
            try:
                dt = self.core.get_device_type(d)
            except Exception:
                dt = "?"
            print("  {} [{}]".format(d, dt))

    def show_available(self, library):
        names = vec(self.core.get_available_devices(library))
        print("Available in {} (not necessarily present in microscope):".format(library))
        for n in names:
            print("  " + n)

    def show_info(self, label=None):
        names = [self.hw.device(label)] if label else self.hw.devices
        for dev in names:
            print("{}:".format(dev))
            self.safe("Library", lambda d=dev: self.core.get_device_library(d))
            self.safe("Adapter", lambda d=dev: self.core.get_device_name(d))
            self.safe("Type", lambda d=dev: self.core.get_device_type(d))
            self.safe("Description", lambda d=dev: self.core.get_device_description(d))

    def show_state(self, label):
        dev = self.hw.device(label)
        idx, name = self.hw.get_state(dev)
        print("{}: state={} / label={}".format(dev, idx, name))

    def show_states(self):
        for short in STATE_ALIASES:
            try:
                self.show_state(short)
            except Exception as exc:
                print("{}: unavailable ({})".format(short, exc))

    def show_labels(self, label):
        dev = self.hw.device(label)
        print("{} state labels:".format(dev))
        for index, name in enumerate(self.hw.labels(dev)):
            print("  {}: {}".format(index, name))

    def show_property(self, label, prop):
        dev = self.hw.device(label)
        val = self.hw.get_property(dev, prop)
        print("{}.{} = {}".format(dev, prop, val))
        self.safe("Read only", lambda: self.core.is_property_read_only(dev, prop))
        self.safe("Type", lambda: self.core.get_property_type(dev, prop))
        self.safe("Pre-init", lambda: self.core.is_property_pre_init(dev, prop))
        try:
            allowed = vec(self.core.get_allowed_property_values(dev, prop))
            if allowed:
                print("  Allowed: {}".format(", ".join(allowed)))
        except Exception:
            pass
        try:
            if self.core.has_property_limits(dev, prop):
                print("  Reported range: {} .. {}".format(
                    self.core.get_property_lower_limit(dev, prop),
                    self.core.get_property_upper_limit(dev, prop)))
        except Exception:
            pass

    def show_properties(self, label):
        dev = self.hw.device(label)
        names = self.hw.property_names(dev)
        print("{} ({} properties):".format(dev, len(names)))
        for name in names:
            try:
                value = self.hw.get_property(dev, name)
            except Exception as exc:
                value = "<read error: {}>".format(exc)
            try:
                writable = " [RO]" if self.core.is_property_read_only(dev, name) else " [RW]"
            except Exception:
                writable = ""
            print("  {} = {}{}".format(name, value, writable))
            try:
                allowed = vec(self.core.get_allowed_property_values(dev, name))
                if allowed:
                    print("    allowed: {}".format(", ".join(allowed)))
            except Exception:
                pass
            try:
                if self.core.has_property_limits(dev, name):
                    print("    range: {} .. {}".format(
                        self.core.get_property_lower_limit(dev, name),
                        self.core.get_property_upper_limit(dev, name)))
            except Exception:
                pass

    def show_status(self):
        self.safe("Z (um)", self.hw.get_z)
        self.safe("XY (um)", self.hw.get_xy)
        self.show_states()
        try:
            self.show_properties("lamp")
        except Exception as exc:
            print("Lamp query unavailable: {}".format(exc))

    def show_all(self):
        for dev in self.hw.devices:
            try:
                self.show_properties(dev)
            except Exception as exc:
                print("{}: unavailable ({})".format(dev, exc))

    def show_roles(self):
        self.safe("Focus", self.core.get_focus_device)
        self.safe("XY", self.core.get_xy_stage_device)
        self.safe("Shutter", self.core.get_shutter_device)
        self.safe("Camera", self.core.get_camera_device)
        self.safe("AutoShutter", self.core.get_auto_shutter)

    def show_configs(self, group=None):
        if group:
            names = vec(self.core.get_available_configs(group))
            print("{} presets ({}):".format(group, len(names)))
            for name in names:
                print("  " + name)
        else:
            names = vec(self.core.get_available_config_groups())
            print("Config groups ({}):".format(len(names)))
            for name in names:
                print("  " + name)

    def set_shortcut(self, alias, value):
        if value is None:
            self.show_state(alias)
            return
        try:
            index = int(value)
        except ValueError:
            self.hw.set_label(alias, value)
        else:
            self.hw.set_state(alias, index)
        self.sent("{} -> {}".format(alias, value))

    def dispatch(self, raw):
        self.last_error = None
        try:
            words = shlex.split(raw)
        except ValueError as exc:
            self.last_error = str(exc)
            print("COMMAND ERROR: {}".format(exc))
            return True
        if not words or words[0].startswith("#"):
            return True
        cmd, args = words[0].lower(), words[1:]
        try:
            if cmd in ("quit", "exit", "q") and not args:
                return False
            if cmd == "help" and not args:
                print(HELP)
            elif cmd == "devices" and not args:
                self.show_devices()
            elif cmd == "available" and len(args) <= 1:
                self.show_available(args[0] if args else "ZeissCAN29")
            elif cmd == "info" and len(args) <= 1:
                self.show_info(args[0] if args else None)
            elif cmd == "status" and not args:
                self.show_status()
            elif cmd == "all" and not args:
                self.show_all()
            elif cmd == "z" and not args:
                print("Z = {} um".format(self.hw.get_z()))
            elif cmd == "xy" and not args:
                x, y = self.hw.get_xy()
                print("XY = ({}, {}) um".format(x, y))
            elif cmd == "states" and not args:
                self.show_states()
            elif cmd == "state" and len(args) == 1:
                self.show_state(args[0])
            elif cmd == "labels" and len(args) == 1:
                self.show_labels(args[0])
            elif cmd == "property" and len(args) == 2:
                self.show_property(*args)
            elif cmd == "properties" and len(args) == 1:
                self.show_properties(args[0])
            elif cmd == "busy" and len(args) == 1:
                dev = self.hw.device(args[0])
                print("{} busy: {}".format(dev, self.core.device_busy(dev)))
            elif cmd == "roles" and not args:
                self.show_roles()
            elif cmd == "shutter-status" and len(args) <= 1:
                label = args[0] if args else None
                print("Shutter{}: {}".format(" " + label if label else "", self.hw.get_shutter(label)))
            elif cmd == "autoshutter" and not args:
                print("AutoShutter: {}".format(self.core.get_auto_shutter()))
            elif cmd == "configs" and len(args) <= 1:
                self.show_configs(args[0] if args else None)
            elif cmd == "version" and not args:
                print("Core: {}".format(self.core.get_version_info()))
                self.safe("Device API", self.core.get_api_version_info)
            elif cmd == "move-z" and len(args) == 1:
                self.hw.move_z(args[0]); self.sent("move-z {} um".format(args[0]))
            elif cmd == "jog-z" and len(args) == 1:
                self.hw.jog_z(args[0]); self.sent("jog-z {} um".format(args[0]))
            elif cmd == "move-xy" and len(args) == 2:
                self.hw.move_xy(*args); self.sent("move-xy {} {} um".format(*args))
            elif cmd == "jog-xy" and len(args) == 2:
                self.hw.jog_xy(*args); self.sent("jog-xy {} {} um".format(*args))
            elif cmd == "jog-x" and len(args) == 1:
                self.hw.jog_xy(args[0], 0); self.sent("jog-x {} um".format(args[0]))
            elif cmd == "jog-y" and len(args) == 1:
                self.hw.jog_xy(0, args[0]); self.sent("jog-y {} um".format(args[0]))
            elif cmd in STATE_ALIASES and len(args) <= 1:
                self.set_shortcut(cmd, args[0] if args else None)
            elif cmd == "set-state" and len(args) == 2:
                self.hw.set_state(*args); self.sent("set-state {} {}".format(*args))
            elif cmd == "set-label" and len(args) == 2:
                self.hw.set_label(*args); self.sent("set-label {} {}".format(*args))
            elif cmd == "set-property" and len(args) == 3:
                self.hw.set_property(*args); self.sent("set-property {} {} {}".format(*args))
            elif cmd == "lamp" and not args:
                self.show_properties(LAMP_DEVICE)
            elif cmd == "lamp" and len(args) == 2:
                self.hw.set_property(LAMP_DEVICE, *args)
                self.sent("lamp property {} = {}".format(*args))
            elif cmd == "shutter" and len(args) in (1, 2):
                if len(args) == 1:
                    opened, label = switch(args[0]), None
                else:
                    label, opened = args[0], switch(args[1])
                self.hw.set_shutter(opened, label)
                self.sent("shutter {} {}".format(label or "default", "open" if opened else "close"))
            elif cmd == "autoshutter" and len(args) == 1:
                self.core.set_auto_shutter(switch(args[0]))
                self.sent("autoshutter {}".format(args[0]))
            elif cmd == "stop" and len(args) == 1:
                self.hw.stop(args[0]); self.sent("stop {}".format(args[0]))
            elif cmd == "wait" and len(args) == 1:
                self.hw.wait(args[0]); print("{} idle".format(args[0]))
            elif cmd == "home" and len(args) == 1:
                self.hw.home(args[0]); self.sent("home {}".format(args[0]))
            elif cmd == "zero-z" and not args:
                self.hw.zero_z(); self.sent("zero-z")
            elif cmd == "zero-xy" and not args:
                self.hw.zero_xy(); self.sent("zero-xy")
            elif cmd == "preset" and len(args) == 2:
                self.core.set_config(*args); self.sent("preset {} {}".format(*args))
            elif cmd == "timeout" and len(args) == 1:
                self.core.set_timeout_ms(int(args[0])); self.sent("timeout {} ms".format(args[0]))
            elif cmd == "core" and len(args) >= 1:
                result = self.hw.call_core(args[0], args[1:])
                if result is not None:
                    try:
                        result = vec(result)
                    except Exception:
                        pass
                print("Core {} -> {}".format(args[0], result))
            else:
                raise ValueError("Unknown command or incorrect arguments. Type 'help'.")
        except Exception as exc:
            self.last_error = str(exc)
            print("COMMAND ERROR: {}".format(exc))
        return True


def main(argv=None):
    folder = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Direct complete Zeiss control via existing MMCoreJ Java backend")
    parser.add_argument("--mm-dir", default=r"C:\Program Files\Micro-Manager-2.0")
    parser.add_argument("--config", default=str(folder / "zeiss_only.cfg"))
    exclusive = parser.add_mutually_exclusive_group()
    exclusive.add_argument("--command", help="One command, then exit")
    exclusive.add_argument("--commands-file", help="Execute lines from a command text file, then exit")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    if not Path(args.mm_dir).is_dir():
        print("Micro-Manager directory not found: {}".format(args.mm_dir))
        return 2
    if not Path(args.config).is_file():
        print("Zeiss config not found: {}".format(args.config))
        return 2

    try:
        from pycromanager import Core, start_headless, stop_headless
    except Exception:
        print("Pycro-Manager import failed (check previous mm38 compatibility patch):")
        traceback.print_exc()
        return 3

    started, core, console = False, None, None
    code = 0
    try:
        start_headless(mm_app_path=args.mm_dir, config_file=args.config,
                       python_backend=False, debug=args.debug)
        started = True
        core = Core()
        console = ZeissConsole(core)
        print("HEADLESS CONNECTED: {} loaded devices".format(len(console.hw.devices)))
        console.show_devices()
        print("No software travel limits or movement confirmations; type 'help'.")
        if args.command is not None:
            console.dispatch(args.command)
            if console.last_error:
                code = 1
        elif args.commands_file is not None:
            with open(args.commands_file, "r", encoding="utf-8-sig") as stream:
                for raw in stream:
                    raw = raw.strip()
                    if not raw or raw.startswith("#"):
                        continue
                    print("zeiss> {}".format(raw))
                    if not console.dispatch(raw):
                        break
                    if console.last_error:
                        code = 1
                        break
        else:
            while True:
                try:
                    command = input("zeiss> ")
                except (EOFError, KeyboardInterrupt):
                    print("\nExiting")
                    break
                if not console.dispatch(command):
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
                print("Headless backend stopped")
            except Exception as exc:
                print("Shutdown warning: {}".format(exc))
                code = 1
        gc.collect()
    return code


if __name__ == "__main__":
    sys.exit(main())
