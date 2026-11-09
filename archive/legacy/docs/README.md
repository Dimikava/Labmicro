# Zeiss Axio Observer Z1 — Headless complete controller v3

**Environment already verified on the laboratory PC**

- Windows 7 Ultimate SP1 x64
- Python 3.8.10 in `mm38`
- Micro-Manager 2.0 at `C:\Program Files\Micro-Manager-2.0`
- Pycro-Manager 1.0.2 Java-headless backend, with the already applied Python 3.8 `mmpycorex` compatibility patch
- Serial COM6 at 57600 baud

## Files

- `zeiss_headless_complete_v3.py` — reusable hardware API + interactive terminal controller
- `zeiss_only.cfg` — extracted from your original `complete.cfg`; camera excluded
- `test_zeiss_headless_complete_v3.py` — offline mock tests

## Run

Copy `zeiss_headless_complete_v3.py` and `zeiss_only.cfg` to `D:\Users\zeiss\Desktop\microGod`.
Close the Micro-Manager GUI, LabVIEW, and other programs that might have COM6 open.

```bat
cd /d D:\Users\zeiss\Desktop\microGod
mm38\Scripts\activate
python zeiss_headless_complete_v3.py
```

No Python reinstall, Micro-Manager reinstall, or additional SDK is required for the Zeiss-only controller if your v2 environment is working.

To see all commands in the interactive console:

```text
zeiss> help
```

## Configured microscope devices

Your `zeiss_only.cfg` includes the following ZeissCAN29 devices:

| Label | Shortcut | Commands |
|---|---|---|
| `ZeissScope` | `scope` | `info`, `properties`, `property`, `set-property` |
| `ZeissFocusAxis` | `focus` | `z`, `move-z`, `jog-z`, `stop`, `wait`, `home`, `zero-z` |
| `ZeissXYStage` | `stage` | `xy`, `move-xy`, `jog-xy`, `jog-x`, `jog-y`, `stop`, `wait`, `home`, `zero-xy` |
| `ZeissObjectiveTurret` | `objective` | `objective`, `labels`, `set-state`, `set-label` |
| `ZeissReflectorTurret` | `reflector` | `reflector`, `labels`, `set-state`, `set-label` |
| `ZeissTubeLens` | `tube` | `tube`, `labels`, `set-state`, `set-label` |
| `ZeissSidePort` | `side` | `side`, `labels`, `set-state`, `set-label` |
| `ZeissBasePort` | `base` | `base`, `labels`, `set-state`, `set-label` |
| `ZeissExcitationSwitcher` | `excitation` | `excitation`, `labels`, `set-state`, `set-label` |
| `ZeissHalogenLamp` | `lamp` | `lamp`, `properties`, `set-property` |

`COM6` is also loaded as a SerialManager device.

**Shutters, condenser, diaphragms, Colibri, and similar ZeissCAN29 mechanisms are not automatically configured.** The command `available` shows adapter device classes supported by the installed library, not which mechanisms are present and controllable on your microscope. The `shutter` command operates only if a valid shutter device is loaded and selected, or explicitly named.

The **PixeLINK camera is deliberately not included** because its adapter was being diagnosed separately.

## Direct-control examples

Commands below are *examples*, not recommended motion positions for your hardware:

```text
zeiss> status
zeiss> devices
zeiss> labels objective
zeiss> objective 1
zeiss> reflector 2
zeiss> side "1-100%L"
zeiss> base 0
zeiss> tube 1
zeiss> excitation 0
zeiss> move-z 100.0
zeiss> jog-z 0.5
zeiss> move-xy 1500 2300
zeiss> jog-xy 5 -5
zeiss> properties lamp
zeiss> property lamp "<exact property name>"
zeiss> lamp "<exact property name>" "<supported property value>"
zeiss> autoshutter off
zeiss> roles
zeiss> configs
zeiss> available ZeissCAN29
```

**No hard-coded lamp intensity/on/off property name:** run `lamp` to inspect the exact properties exposed on your particular device; then write values using `lamp NAME VALUE`.

**Unrestricted Java Core method access** for commands not explicitly wrapped by the program:

```text
zeiss> core get_position ZeissFocusAxis
zeiss> core set_position ZeissFocusAxis float:100.0
zeiss> core get_loaded_devices
```

Typed arguments: `int:3`, `float:2.5`, `bool:true`, `str:mytext`; unprefixed arguments are treated as strings. This is an unrestricted method invocation path to Micro-Manager Core. It uses the Python-facing **snake_case** method names employed by Pycro-Manager.

## One-shot / batch control

```bat
python zeiss_headless_complete_v3.py --command "jog-z 0.5"
python zeiss_headless_complete_v3.py --commands-file operations.txt
```

`operations.txt` example:

```text
# Only commands intentionally placed in this file will run
z
xy
objective 1
jog-z 0.5
wait focus
status
```

For frequent operations, keep the interactive controller open to avoid repeatedly initializing the microscope.

## For future Python API / GUI use

The same module exposes `ZeissHardware(core)` for another Python program. Once a Pycro-Manager `Core()` instance is connected:

```python
from zeiss_headless_complete_v3 import ZeissHardware

hardware = ZeissHardware(core)
print(hardware.get_z())
print(hardware.get_xy())
hardware.jog_z(0.5)
hardware.set_state('objective', 1)
hardware.set_property('lamp', '<exact property name>', '<supported value>')
```

Do not run two independent Java headless backends against COM6 simultaneously.

## Verification

Offline tests:

```bat
python -m unittest -v test_zeiss_headless_complete_v3.py
```

The scripts have passed syntax verification and **15 mock-based offline tests**. The extended v3 hardware commands have not been tested against the physical microscope. You have already confirmed direct Z motion under v2. Additional optical/XY controls depend on the actual motors and property support.

## Controller behavior

- No software travel limits, maximum step settings, arming, or per-command confirmations.
- No automatic movement program is executed after startup; loading the Micro-Manager configuration can still affect hardware state.
- The `home`, `zero-z`, `zero-xy` and `core` commands change hardware or coordinate reference state if invoked; they are intentionally exposed with no extra confirmation.
- MMCore errors are printed without terminating the interactive console.
- On quit, the Java-headless backend is stopped; the microscope is not homed or automatically moved by the script.
- Device-controller firmware and physical interlocks, if present, remain outside Python control.
