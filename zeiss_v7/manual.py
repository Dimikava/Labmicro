"""Headless Zeiss Axio Observer Z1 controller v5, using Micro-Manager 2.0's Java Core.

Target: Windows 7 SP1 x64, Python 3.8.10, pycromanager 1.0.2 and the
already working Java-headless setup. Uses zeiss_only.cfg (no PixeLINK camera).

This controller does NOT add software travel limits, confirmations, or arm flags.
Commands go straight to the Micro-Manager Core. Loading the config may itself
change hardware state. Firmware/hardware controls remain in force.

The ZeissHardware class can also be imported into another Python application.
"""

import argparse
import csv
import itertools
import time
import gc
import json
import math
import re
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
  home DEVICE                          Native hardware homing, if supported (XY may find lower stops)
  mark-home NAME                       Capture XYZ as a named home (persistent)
  set-home NAME X Y Z                  Save explicit XYZ home coordinates in um
  go-home NAME                         Move to named XYZ home
  forget-home NAME                     Delete a named home
  homes                                List all named XYZ homes
  mark-home z|xy                       Legacy: save Z-only or XY-only home
  go-home z|xy                         Legacy: move Z or XY to its saved home
  forget-home z|xy                     Legacy: delete legacy Z/XY home
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

PERSISTENT XYZ STEP SEQUENCES (NO SOFTWARE TRAVEL LIMITS OR CONFIRMATIONS)
  sequence create NAME HOME NX NY NZ DX DY DZ [DWELL_MS]
       NX,NY,NZ are number of INTERVALS (positions = steps+1).
       DX,DY,DZ are signed total distances in um relative to saved home.
       E.g. sequence create raster1 specimen1 4 2 0 20 10 0
  sequence create-step NAME HOME NX NY NZ SX SY SZ [DWELL_MS]
       Alternative: SX,SY,SZ are signed distances PER STEP in um.
  sequence list                        List saved sequences
  sequence show NAME                   Origin XYZ, interval counts, step sizes, distances
  sequence preview NAME [COUNT]        Show first COUNT positions (default 15)
  sequence export NAME CSV_FILE        Export all planned XYZ positions to CSV
  sequence refresh-home NAME           Update a sequence's snapshot of its home
  sequence run NAME                    Go to saved XYZ, then execute serpentine grid
  sequence delete NAME                 Delete sequence

SHORTCUTS FOR EVERY CONFIGURED STATE DEVICE
  objective [INDEX|LABEL]              Objective turret read or set
  reflector [INDEX|LABEL]              Reflector turret read or set
  tube [INDEX|LABEL]                   Tube lens read or set
  side [INDEX|LABEL]                   Side optical port read or set
  base [INDEX|LABEL]                   Base optical port read or set
  excitation [INDEX|LABEL]             Excitation switcher read or set

LAMP / GENERIC CORE ACCESS
  lamp                                 Display all halogen lamp properties
  lamp-controls                        Discover writable numeric lamp property, units and limits
  lamp-intensity [VALUE]               Read/write selected numeric lamp control (native unit)
  lamp-volt [VOLTS]                    Read/write if voltage-labelled property exists (V or mV)
  lamp NAME VALUE                      Write exact halogen lamp property
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
User-defined homes are absolute XYZ coordinates saved in zeiss_user_homes.json;
sequences are saved in zeiss_sequences.json. They do NOT reference hardware.
Sequence order: X fastest, Y next, Z slowest, with serpentine reversal.
'Home' is captured into a sequence when it is created: change it with
'sequence refresh-home NAME'. Stage positions and all distances are in um.
The native XY home can move to hardware end stops. PixeLINK is NOT loaded.
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

    def lamp_numeric_controls(self):
        """Discover writable numeric servo properties; preserve native units."""
        dev = self.device(LAMP_DEVICE)
        candidates = []
        for name in self.property_names(dev):
            try:
                if self.core.is_property_read_only(dev, name):
                    continue
                value = float(self.core.get_property(dev, name))
                if not math.isfinite(value):
                    continue
            except Exception:
                continue
            lower = upper = None
            try:
                if self.core.has_property_limits(dev, name):
                    lower = float(self.core.get_property_lower_limit(dev, name))
                    upper = float(self.core.get_property_upper_limit(dev, name))
            except Exception:
                pass
            candidates.append((name, value, lower, upper))
        return candidates

    @staticmethod
    def lamp_voltage_unit(name):
        """Return native volts scaling if a field explicitly describes voltage."""
        unit = re.sub(r"[^a-z]", "", name.lower())
        if unit in ("v", "volt", "volts", "voltage") or unit.startswith("voltage"):
            return 1.0
        if unit in ("mv", "millivolt", "millivolts", "millivoltage"):
            return 1000.0
        return None

    def lamp_control(self, voltage_only=False):
        candidates = self.lamp_numeric_controls()
        if voltage_only:
            candidates = [c for c in candidates if self.lamp_voltage_unit(c[0]) is not None]
        if not candidates:
            raise ValueError("No {}writable numeric lamp property found. Run 'lamp-controls'.".format(
                "voltage-labelled " if voltage_only else ""))
        if len(candidates) == 1:
            return candidates[0]
        preferred = [c for c in candidates if re.sub(r"[^a-z]", "", c[0].lower())
                     in ("intensity", "brightness", "power")]
        if not voltage_only and len(preferred) == 1:
            return preferred[0]
        raise ValueError("Several numeric lamp properties: {}. Use 'lamp PROPERTY VALUE'.".format(
            ", ".join(c[0] for c in candidates)))

    def lamp_intensity(self, value=None):
        name, current, lower, upper = self.lamp_control()
        if value is not None:
            # The unit is dictated by the Zeiss adapter, not necessarily volts.
            self.set_property(LAMP_DEVICE, name, str(num(value)))
        return name, current, lower, upper

    def lamp_volts(self, value=None):
        name, current, lower, upper = self.lamp_control(voltage_only=True)
        factor = self.lamp_voltage_unit(name)
        if value is not None:
            self.set_property(LAMP_DEVICE, name, str(num(value) * factor))
        return name, current / factor, (lower / factor if lower is not None else None), (upper / factor if upper is not None else None)

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


class HomeStore:
    """Persist user-defined absolute positions without modifying stage origins."""

    def __init__(self, path):
        self.path = Path(path)
        self.positions = {}
        if self.path.is_file():
            with self.path.open("r", encoding="utf-8") as f:
                value = json.load(f)
            if not isinstance(value, dict):
                raise ValueError("User homes file must contain a JSON object")
            self.positions = value

    def save(self):
        # File stores only user-saved coordinate values; microscope .cfg is untouched.
        temp = self.path.with_name(self.path.name + ".tmp")
        with temp.open("w", encoding="utf-8") as f:
            json.dump(self.positions, f, indent=2, sort_keys=True)
            f.write("\n")
        temp.replace(self.path)

    @staticmethod
    def kind(text):
        t = text.lower()
        if t in ("z", "focus"):
            return "z"
        if t in ("xy", "stage"):
            return "xy"
        raise ValueError("Choose z or xy")

    def mark(self, kind, hw):
        k = self.kind(kind)
        value = hw.get_z() if k == "z" else list(hw.get_xy())
        self.positions[k] = value
        self.save()
        return value

    def goto(self, kind, hw):
        k = self.kind(kind)
        if k not in self.positions:
            raise ValueError("No saved {} home. Run 'mark-home {}'.".format(k, k))
        value = self.positions[k]
        if k == "z":
            hw.move_z(num(value))
        else:
            if not isinstance(value, list) or len(value) != 2:
                raise ValueError("Invalid saved XY home")
            hw.move_xy(num(value[0]), num(value[1]))
        return value

    def forget(self, kind):
        k = self.kind(kind)
        self.positions.pop(k, None)
        self.save()

    @staticmethod
    def valid_name(name):
        if not name or name.lower() in ("z", "xy", "focus", "stage"):
            raise ValueError("Named XYZ homes cannot be named z, xy, focus, or stage (legacy aliases)")
        if any(ch.isspace() for ch in name):
            raise ValueError("Home names may not contain whitespace")
        return name

    def named(self):
        named = self.positions.get("named", {})
        if not isinstance(named, dict):
            raise ValueError("Invalid named homes structure in JSON")
        return named

    def set_xyz(self, name, x, y, z):
        name = self.valid_name(name)
        item = {"x": num(x), "y": num(y), "z": num(z)}
        self.positions.setdefault("named", {})[name] = item
        self.save()
        return item.copy()

    def mark_xyz(self, name, hw):
        x, y = hw.get_xy()
        z = hw.get_z()
        return self.set_xyz(name, x, y, z)

    def get_xyz(self, name):
        name = self.valid_name(name)
        try:
            data = self.named()[name]
        except KeyError:
            raise ValueError("No named XYZ home {!r}; use 'homes'".format(name))
        if not isinstance(data, dict):
            raise ValueError("Invalid XYZ home record {!r}".format(name))
        return {axis: num(data[axis]) for axis in ("x", "y", "z")}

    def goto_xyz(self, name, hw):
        point = self.get_xyz(name)
        hw.move_xy(point["x"], point["y"])
        hw.wait("stage")
        hw.move_z(point["z"])
        hw.wait("focus")
        return point

    def forget_xyz(self, name):
        name = self.valid_name(name)
        if name not in self.named():
            raise ValueError("No named home {!r}".format(name))
        del self.positions["named"][name]
        self.save()


class SequenceStore:
    """Persistent XYZ serpentine-grid plans with a frozen named-home origin."""

    AXES = ("x", "y", "z")

    def __init__(self, path):
        self.path = Path(path)
        self.sequences = {}
        if self.path.is_file():
            with self.path.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
            if not isinstance(data, dict):
                raise ValueError("Sequences JSON must be an object")
            self.sequences = data

    @staticmethod
    def sequence_name(name):
        if not name or any(ch.isspace() for ch in name):
            raise ValueError("Sequence names must be nonempty and contain no whitespace")
        return name

    @staticmethod
    def checked_steps(value):
        result = int(value)
        if str(result) != str(value):
            raise ValueError("Axis intervals must be integers >= 0")
        if result < 0:
            raise ValueError("Axis intervals must be integers >= 0")
        return result

    def save(self):
        temp = self.path.with_name(self.path.name + ".tmp")
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(self.sequences, stream, indent=2, sort_keys=True)
            stream.write("\n")
        temp.replace(self.path)

    def create(self, name, home_name, intervals, distances, dwell_ms, home_store):
        name = self.sequence_name(name)
        home = home_store.get_xyz(home_name)
        steps = dict(zip(self.AXES, [self.checked_steps(n) for n in intervals]))
        travel = dict(zip(self.AXES, [num(d) for d in distances]))
        for axis in self.AXES:
            if steps[axis] == 0 and travel[axis] != 0:
                raise ValueError("{} distance must be 0 when {} steps = 0".format(axis.upper(), axis.upper()))
        dwell = num(dwell_ms)
        if dwell < 0:
            raise ValueError("Dwell milliseconds must be nonnegative")
        spec = {"home_name": home_name, "origin_um": home, "steps": steps,
                "distance_um": travel, "dwell_ms": dwell, "scan": "serpentine-grid"}
        self.sequences[name] = spec
        self.save()
        return spec

    def get(self, name):
        name = self.sequence_name(name)
        if name not in self.sequences:
            raise ValueError("No saved sequence {!r}; use 'sequence list'".format(name))
        spec = self.sequences[name]
        # Validate disk data before any motion (no travel limits imposed).
        for axis in self.AXES:
            num(spec["origin_um"][axis])
            num(spec["distance_um"][axis])
            n = self.checked_steps(spec["steps"][axis])
            if n == 0 and num(spec["distance_um"][axis]) != 0:
                raise ValueError("Nonzero {} distance with zero steps".format(axis))
        if num(spec["dwell_ms"]) < 0:
            raise ValueError("Invalid negative dwell")
        return spec

    def refresh_home(self, name, home_store):
        spec = self.get(name)
        spec["origin_um"] = home_store.get_xyz(spec["home_name"])
        self.save()
        return spec

    def delete(self, name):
        self.get(name)
        del self.sequences[name]
        self.save()

    @classmethod
    def stepsize(cls, spec):
        return {axis: (spec["distance_um"][axis] / spec["steps"][axis]
                       if spec["steps"][axis] else 0.0) for axis in cls.AXES}

    @classmethod
    def metrics(cls, spec):
        sx, sy, sz = [spec["steps"][axis] for axis in cls.AXES]
        dx, dy, dz = [spec["distance_um"][axis] for axis in cls.AXES]
        count = (sx + 1) * (sy + 1) * (sz + 1)
        # Serpentine path: all motion is axis-aligned, one axis at a time.
        length = abs(dx) * (sy + 1) * (sz + 1) + abs(dy) * (sz + 1) + abs(dz)
        displacement = math.sqrt(dx * dx + dy * dy + dz * dz)
        return count, length, displacement

    @classmethod
    def points(cls, spec):
        """Stream (index, x, y, z) in X-fastest serpentine XYZ grid order."""
        sx, sy, sz = [spec["steps"][a] for a in cls.AXES]
        step = cls.stepsize(spec)
        origin = spec["origin_um"]
        index = 0
        row = 0
        for zi in range(sz + 1):
            yrange = range(sy + 1) if zi % 2 == 0 else range(sy, -1, -1)
            for yi in yrange:
                xrange = range(sx + 1) if row % 2 == 0 else range(sx, -1, -1)
                for xi in xrange:
                    yield (index,
                           origin["x"] + xi * step["x"],
                           origin["y"] + yi * step["y"],
                           origin["z"] + zi * step["z"])
                    index += 1
                row += 1

    @classmethod
    def export_csv(cls, spec, path):
        path = Path(path)
        previous = None
        cumulative = 0.0
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(("point_index", "x_um", "y_um", "z_um", "delta_um", "cumulative_um"))
            for index, x, y, z in cls.points(spec):
                if previous is None:
                    delta = 0.0
                else:
                    delta = math.sqrt((x-previous[0])**2 + (y-previous[1])**2 + (z-previous[2])**2)
                cumulative += delta
                writer.writerow((index, x, y, z, delta, cumulative))
                previous = (x, y, z)
        return path, cumulative

    @classmethod
    def run(cls, spec, hw, progress=print):
        count, length, displacement = cls.metrics(spec)
        previous = None
        completed = 0
        dwell = num(spec["dwell_ms"])
        try:
            for idx, x, y, z in cls.points(spec):
                if previous is None or (x, y) != (previous[0], previous[1]):
                    hw.move_xy(x, y)
                    hw.wait("stage")
                if previous is None or z != previous[2]:
                    hw.move_z(z)
                    hw.wait("focus")
                previous = (x, y, z)
                completed += 1
                progress("[{:d}/{:d}] XYZ = ({:.6f}, {:.6f}, {:.6f}) um".format(
                    completed, count, x, y, z))
                if dwell:
                    time.sleep(dwell / 1000.0)
        except KeyboardInterrupt:
            for dev in ("stage", "focus"):
                try:
                    hw.stop(dev)
                except Exception:
                    pass
            raise RuntimeError("Sequence interrupted at {}/{} points; stop requested".format(completed, count))
        return completed, length


class ZeissConsole:
    def __init__(self, core, home_store=None, sequence_store=None):
        self.hw = ZeissHardware(core)
        self.core = core
        self.homes = home_store
        self.sequences = sequence_store
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

    def show_lamp_controls(self):
        dev = self.hw.device(LAMP_DEVICE)
        candidates = self.hw.lamp_numeric_controls()
        self.show_properties(dev)
        print("Detected numeric writable controls:")
        if not candidates:
            print("  NONE. No automatic lamp intensity/voltage control available.")
        for name, value, low, high in candidates:
            print("  {} = {} (native units; range {} .. {})".format(name, value, low, high))
            factor = self.hw.lamp_voltage_unit(name)
            if factor is not None:
                print("    Voltage-equivalent = {} V".format(value / factor))

    def show_lamp_auto(self, volts=False, value=None):
        if volts:
            name, current, low, high = self.hw.lamp_volts(value)
            if value is None:
                print("Lamp {} = {} V (range {} .. {} V)".format(name, current, low, high))
            else:
                print("SENT: lamp-volt {} V through property {}".format(value, name))
        else:
            name, current, low, high = self.hw.lamp_intensity(value)
            if value is None:
                print("Lamp {} = {} native units (range {} .. {})".format(name, current, low, high))
            else:
                print("SENT: lamp-intensity {} native units through property {}".format(value, name))

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

    def show_homes(self):
        if self.homes is None:
            print("Saved homes unavailable (no positions path configured)")
            return
        named = self.homes.named()
        print("Named XYZ homes (um) ({}):".format(len(named)))
        for name in sorted(named):
            p = self.homes.get_xyz(name)
            print("  {}: X={:.6f}, Y={:.6f}, Z={:.6f}".format(name, p["x"], p["y"], p["z"]))
        for legacy in ("z", "xy"):
            if legacy in self.homes.positions:
                print("  [legacy {}] = {}".format(legacy, self.homes.positions[legacy]))

    def show_sequence(self, name):
        if self.sequences is None:
            raise ValueError("No sequences store configured")
        spec = self.sequences.get(name)
        count, length, displacement = SequenceStore.metrics(spec)
        increments = SequenceStore.stepsize(spec)
        p, steps, distance = spec["origin_um"], spec["steps"], spec["distance_um"]
        print("Sequence: {} ({}; saved home {!r})".format(name, spec["scan"], spec["home_name"]))
        print("       home (um)       steps (intervals)    distance (um)    step size (um)")
        for axis in SequenceStore.AXES:
            print("  {}   {:>12.6f}   {:>12d}       {:>12.6f}   {:>12.6f}".format(
                axis.upper(), p[axis], steps[axis], distance[axis], increments[axis]))
        print("  Total positions: {} (Nx+1)*(Ny+1)*(Nz+1)".format(count))
        print("  Home-to-corner straight distance: {:.6f} um".format(displacement))
        print("  Planned serpentine traveled distance: {:.6f} um".format(length))
        print("  Dwell per position: {} ms".format(spec["dwell_ms"]))
        print("  Home coordinates are captured at sequence creation, not live-linked.")

    def sequence_cmd(self, args):
        if self.sequences is None or self.homes is None:
            raise ValueError("Homes and sequences stores must both be configured")
        if not args:
            raise ValueError("Type 'help' for sequence commands")
        sub = args[0].lower()
        rest = args[1:]
        if sub == "create" and len(rest) in (8, 9):
            name, home = rest[:2]
            spec = self.sequences.create(name, home, rest[2:5], rest[5:8],
                                         rest[8] if len(rest) == 9 else 0, self.homes)
            print("Saved sequence {!r}.".format(name))
            self.show_sequence(name)
        elif sub == "create-step" and len(rest) in (8, 9):
            name, home = rest[:2]
            steps = [SequenceStore.checked_steps(n) for n in rest[2:5]]
            step_sizes = [num(x) for x in rest[5:8]]
            distance = [n * unit for n, unit in zip(steps, step_sizes)]
            self.sequences.create(name, home, steps, distance,
                                  rest[8] if len(rest) == 9 else 0, self.homes)
            print("Saved step-size sequence {!r}.".format(name))
            self.show_sequence(name)
        elif sub == "list" and not rest:
            print("Saved sequences ({}):".format(len(self.sequences.sequences)))
            for name in sorted(self.sequences.sequences):
                spec = self.sequences.get(name)
                count, length, _ = SequenceStore.metrics(spec)
                print("  {}: home={} positions={} path={:.4f} um".format(
                    name, spec["home_name"], count, length))
        elif sub == "show" and len(rest) == 1:
            self.show_sequence(rest[0])
        elif sub == "preview" and len(rest) in (1, 2):
            name = rest[0]
            count = int(rest[1]) if len(rest) == 2 else 15
            if count < 1:
                raise ValueError("Preview count must be >= 1")
            spec = self.sequences.get(name)
            total, _, _ = SequenceStore.metrics(spec)
            print("Preview {!r}: first {} of {} XYZ points (um)".format(name, min(count, total), total))
            for i, x, y, z in itertools.islice(SequenceStore.points(spec), count):
                print("  {:>6d}: {:>12.6f} {:>12.6f} {:>12.6f}".format(i, x, y, z))
            if count < total:
                print("  ... {} positions not shown".format(total-count))
        elif sub == "refresh-home" and len(rest) == 1:
            self.sequences.refresh_home(rest[0], self.homes)
            print("Updated origin from saved home for {!r}".format(rest[0]))
        elif sub == "export" and len(rest) == 2:
            path, total = SequenceStore.export_csv(self.sequences.get(rest[0]), rest[1])
            print("Exported {!r} to {} (path length {:.6f} um)".format(rest[0], path, total))
        elif sub == "run" and len(rest) == 1:
            name = rest[0]
            spec = self.sequences.get(name)
            print("Executing {!r} (no additional software motion limits or confirmations)...".format(name))
            completed, length = SequenceStore.run(spec, self.hw)
            print("SEQUENCE COMPLETE: {} points; path {:.6f} um".format(completed, length))
        elif sub == "delete" and len(rest) == 1:
            self.sequences.delete(rest[0])
            print("Deleted sequence {!r}".format(rest[0]))
        else:
            raise ValueError("Unknown sequence command or argument count. Type 'help'.")

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
            elif cmd == "lamp-controls" and not args:
                self.show_lamp_controls()
            elif cmd == "lamp-intensity" and len(args) <= 1:
                self.show_lamp_auto(volts=False, value=args[0] if args else None)
            elif cmd == "lamp-volt" and len(args) <= 1:
                self.show_lamp_auto(volts=True, value=args[0] if args else None)
            elif cmd == "homes" and not args:
                self.show_homes()
            elif cmd == "mark-home" and len(args) == 1:
                if self.homes is None:
                    raise ValueError("No positions path configured")
                if args[0].lower() in ("z", "xy", "focus", "stage"):
                    value = self.homes.mark(args[0], self.hw)
                else:
                    value = self.homes.mark_xyz(args[0], self.hw)
                print("Saved home {} = {}".format(args[0], value))
            elif cmd == "set-home" and len(args) == 4:
                if self.homes is None:
                    raise ValueError("No positions path configured")
                value = self.homes.set_xyz(*args)
                print("Saved named XYZ home {} = {}".format(args[0], value))
            elif cmd == "go-home" and len(args) == 1:
                if self.homes is None:
                    raise ValueError("No positions path configured")
                if args[0].lower() in ("z", "xy", "focus", "stage"):
                    value = self.homes.goto(args[0], self.hw)
                else:
                    value = self.homes.goto_xyz(args[0], self.hw)
                self.sent("go-home {} to {}".format(args[0], value))
            elif cmd == "forget-home" and len(args) == 1:
                if self.homes is None:
                    raise ValueError("No positions path configured")
                if args[0].lower() in ("z", "xy", "focus", "stage"):
                    self.homes.forget(args[0])
                else:
                    self.homes.forget_xyz(args[0])
                print("Removed saved home {}".format(args[0]))
            elif cmd == "sequence":
                self.sequence_cmd(args)
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


def main(argv=None, base_dir=None):
    folder = Path(base_dir).resolve() if base_dir is not None else Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Direct complete Zeiss control via existing MMCoreJ Java backend")
    parser.add_argument("--mm-dir", default=r"C:\Program Files\Micro-Manager-2.0")
    parser.add_argument("--config", default=str(folder / "zeiss_only.cfg"))
    exclusive = parser.add_mutually_exclusive_group()
    exclusive.add_argument("--command", help="One command, then exit")
    exclusive.add_argument("--commands-file", help="Execute lines from a command text file, then exit")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--positions", default=str(folder / "zeiss_user_homes.json"),
                        help="Persistent legacy and named XYZ home positions")
    parser.add_argument("--sequences", default=str(folder / "zeiss_sequences.json"),
                        help="Persistent XYZ movement sequences")
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
        console = ZeissConsole(core, HomeStore(args.positions), SequenceStore(args.sequences))
        print("HEADLESS CONNECTED: {} loaded devices".format(len(console.hw.devices)))
        console.show_devices()
        print("No software travel limits or movement confirmations; type 'help'.")
        print("Named XYZ homes and persistent sequences are available in v5.")
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
