# Zeiss Axio Observer Z1 — Headless Controller v4

This is an incremental update to the user's confirmed working v3 controller.

**Environment:** Windows 7 Ultimate SP1 x64, Python 3.8.10 (`mm38`), Micro-Manager 2.0 installed in `C:\Program Files\Micro-Manager-2.0`, Pycro-Manager 1.0.2 Java backend with existing Python 3.8 compatibility patch. Serial COM6, 57600 baud. PixeLINK camera is excluded.

## Installation

Extract the files to `D:\Users\zeiss\Desktop\microGod`. Do not overwrite your working v3 until you have copied it somewhere safe. Keep `zeiss_only.cfg` next to the new script. Do not modify the Micro-Manager installation, serial driver, Java libraries, or config while testing.

With the Micro-Manager GUI and LabVIEW closed:

```bat
cd /d D:\Users\zeiss\Desktop\microGod
mm38\Scripts\activate
python zeiss_headless_complete_v4.py
```

No new Python packages are needed relative to the working v3 installation.

## Lamp control

The ZeissCAN29 adapter implements `ZeissHalogenLamp` as a **Servo**. Its writable numeric property is named dynamically after the scaling unit reported by the microscope, rather than necessarily being `Voltage`. Consequently, do not assume the property is named `Voltage` or that its unit is volts.

Run:

```text
zeiss> lamp-controls
zeiss> lamp-intensity
zeiss> lamp-intensity 3.0
```

`lamp-controls` prints actual properties, numeric writable candidates, and reported ranges. `lamp-intensity VALUE` writes to the selected unambiguous numeric property in its **native unit**. The example `3.0` is illustrative: use a value appropriate to the property and hardware.

If a writable property explicitly uses volts (`V` / `Volt` / `Voltage`) or millivolts (`mV`), these commands become available:

```text
zeiss> lamp-volt
zeiss> lamp-volt 3.0
```

The argument to `lamp-volt` is in **volts**; if the property is `mV`, the script converts to millivolts. If no voltage-labelled property exists, the command stops with an explanatory message (it does **not** invent a property).

Original commands still work:

```text
zeiss> lamp
zeiss> properties lamp
zeiss> lamp "EXACT PROPERTY NAME" "VALUE"
zeiss> set-property lamp "EXACT PROPERTY NAME" "VALUE"
```

**Important:** No live hardware verification has been performed for the new lamp mapping. If you want to verify the unit before any change, first use `lamp-controls` and compare the current value against the Zeiss microscope panel.

## Home positions: hardware versus user-defined

The older ZeissCAN29 source implements `ZeissXYStage.Home()` as a search for the **lower physical hardware stops**. It does not implement `Axis.Home()` equivalently for the Z focus. In the same source, the focus `SetOrigin()` is a no-op. Your installed adapter may differ; functions such as `home focus` and `zero-z` should not be assumed to work on your hardware.

v4 adds user-defined, persistent home positions:

```text
zeiss> mark-home z
zeiss> mark-home xy
zeiss> homes
zeiss> go-home z
zeiss> go-home xy
zeiss> forget-home z
zeiss> forget-home xy
```

`mark-home` stores the microscope's **current absolute coordinates** (without moving). `go-home` sends a normal absolute movement command to the saved coordinate; no confirmation or software travel limit is added. These are not true motor reference operations and do not determine the home switch or encoder origin. The file `zeiss_user_homes.json` is created next to the script; it persists across sessions. If the microscope's coordinate origin changes, previously saved homes may no longer be physically meaningful.

The original `home stage` command remains available and requests native hardware XY homing, which can travel to the hardware end stops. It is not interchangeable with `go-home xy`. `home focus` may fail because this is not implemented for ZeissCAN29 focus.

## Existing functions preserved

`move-z`, `jog-z`, `move-xy`, `jog-xy`, `jog-x`, `jog-y`, `objective`, `reflector`, `side`, `base`, `tube`, `excitation`, `states`, `labels`, `set-property`, `core`, `stop`, `wait`, `shutter`, `autoshutter` and all previous read-only diagnostics are retained. There are no motion limits or confirmations. Initialization alone can affect microscope state.

## Testing

This script passes Python compilation and 23 offline mock tests, including lamp discovery, volts/millivolts conversion, and home persistence. It has **not** been tested against the physical microscope. To run offline tests:

```bat
python -m unittest -v test_zeiss_headless_complete_v4.py
```

For a test run, start with `status`, `lamp-controls`, `homes`, and `mark-home z` before sending new movement commands. You can use `--positions PATH` to store the homes JSON elsewhere. Never run two controllers that both open COM6.
