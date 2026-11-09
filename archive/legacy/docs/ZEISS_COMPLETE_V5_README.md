# Zeiss Axio Observer Z1 — Headless Controller v5

**New in v5:** multiple named XYZ homes; editable/saved raster sequences starting from a named home; X/Y/Z step counts, signed travel distances and derived step sizes; sequence preview, execution and CSV export.

The controller extends the working v4 Java/Pycro-Manager setup. It runs on Windows 7 Ultimate SP1 x64 with Python 3.8.10 and Micro-Manager 2.0 using the already-tested Pycro-Manager headless Java backend. The PixeLINK camera is **not** part of this controller.

## Install

1. **Keep your working v4 file as a backup.** Extract `zeiss_headless_complete_v5.py` into `D:\Users\zeiss\Desktop\microGod`, alongside your working `zeiss_only.cfg`. If an existing `zeiss_only.cfg` works, keep it.
2. **Keep your existing `zeiss_user_homes.json`.** v5 reads the v4 saved `z` and `xy` home entries, and can also store named XYZ homes in the same file. Never overwrite this file with an empty one.
3. No new Python dependencies are needed. Keep the existing patched Python 3.8 `mm38` environment.
4. Close the Micro-Manager GUI, LabVIEW and any other application using COM6. Avoid conflicting serial clients.

Run from Command Prompt:

```bat
cd /d D:\Users\zeiss\Desktop\microGod
mm38\Scripts\activate
python zeiss_headless_complete_v5.py
```

Alternatively, place `start_zeiss_v5.bat` next to the script and double-click it.

## Named XYZ homes

All positions are **absolute stage coordinates in µm**, not physical limit switches. Use a different short name for every location:

```text
zeiss> mark-home sample_A                # capture current X, Y, Z together
zeiss> mark-home sample_B
zeiss> set-home sample_C 1000 2000 150  # manually set coordinates in µm
zeiss> homes                             # display all homes and their XYZ values
zeiss> go-home sample_B                  # move to saved XYZ
zeiss> forget-home sample_C              # delete that home
```

The old v4 legacy commands remain: `mark-home z`, `mark-home xy`, `go-home z`, `go-home xy`, and `forget-home z|xy`. They remain separate from named XYZ homes. Names `z`, `xy`, `focus` and `stage` are reserved for legacy commands.

Homes persist in `zeiss_user_homes.json` next to the script by default. They are **not** hardware homing or origin calibration. If the microscope changes coordinate origins, the stored coordinates may cease to identify the intended physical location.

## Sequences: counts and total distance

Define an XYZ serpentine raster around an existing named home:

```text
zeiss> sequence create scan_A sample_A 4 2 1 20 10 5
```

Fields (after sequence name and home):

| Field | X | Y | Z |
| --- | ---: | ---: | ---: |
| Intervals | 4 | 2 | 1 |
| Total signed travel, µm | +20 | +10 | +5 |
| Resulting increment, µm | +5 | +5 | +5 |
| Number of positions | 5 | 3 | 2 |

This specific command yields **5 × 3 × 2 = 30 positions**. An interval count of 0 means a single point on that axis and requires 0 travel distance. Negative travel distances produce a scan in the opposite direction.

**Important:** The number after each axis is its **number of intervals**, not number of acquired image frames. There are always `intervals + 1` positions on that axis, including the starting point.

Alternatively, enter *per-step* increments instead of total travel distance:

```text
zeiss> sequence create-step scan_B sample_B 10 4 0 2 -3 0
```

This means 10 intervals × +2 µm along X, 4 intervals × -3 µm along Y, and no Z stepping. The program stores total travel distances as `+20, -12, 0 µm`.

Optional final argument for either definition is dwell in milliseconds per point:

```text
zeiss> sequence create scan_C sample_A 4 2 1 20 10 5 200
```

This waits 200 ms after each target position is reached. No image acquisition, camera trigger or exposure is implied by dwell.

## Inspect, preview, export and execute

```text
zeiss> sequence list
zeiss> sequence show scan_A
zeiss> sequence preview scan_A 10
zeiss> sequence export scan_A "D:\Users\zeiss\Desktop\scan_A.csv"
zeiss> sequence run scan_A
zeiss> sequence delete scan_A
```

`sequence show` reports: frozen home XYZ, step counts, step size, axis travel distances, number of positions, straight-line displacement from the starting home to the far corner, and total traveled **serpentine path length**. The path length excludes the microscope's initial approach to the saved home.

The scan visits X positions fastest. X reverses direction at each Y row; Y reverses at each Z plane to avoid unnecessary returns. For each waypoint, the controller commands only changed axes and waits for Micro-Manager to report the axis idle. If dwell is set, the pause occurs afterward.

When a sequence is created, its home coordinates are **snapshotted**. If you later update that named home, the previously saved sequence still uses the old XYZ coordinates until you execute:

```text
zeiss> sequence refresh-home scan_A
```

Sequences persist in `zeiss_sequences.json` next to the script by default; existing sequences can be redefined using `sequence create` with the same name. A sequence still has its saved coordinates even if its named home is later deleted, but `refresh-home` then fails.

`sequence export` creates a CSV with index, target X/Y/Z, segment distance and cumulative distance. This is useful for validating movement paths or driving later acquisition logic.

## Operation and scope

- `sequence create`, `sequence show`, `sequence list`, `sequence preview` and `sequence export` **do not issue movement commands**.
- `go-home`, `sequence run`, and direct movement commands **immediately send movements** without arm flags, per-command confirmations, or custom software travel limits.
- A sequence starts by moving to the saved starting coordinates, then travels through the planned positions. It does **not** return to the starting home automatically afterward.
- `Ctrl+C` during the sequence attempts to stop the Z and XY stage, but whether an in-progress blocking device call can be interrupted depends on the Micro-Manager adapter and hardware.
- A hardware emergency-stop mechanism and the microscope's own protective limits still matter. A software `stop` command is not an emergency stop.
- **No camera acquisition has been added.** This is a positioning/raster-motion controller, not a synchronized scan acquisition controller.
- The existing `move-z`, `jog-z`, `move-xy`, `jog-xy`, `objective`, `reflector`, `side`, `base`, `tube`, `excitation`, `lamp`, `lamp-controls`, `lamp-intensity`, `lamp-volt`, `core`, and general property commands remain available.

## Run without the interactive console

```bat
python zeiss_headless_complete_v5.py --command "sequence show scan_A"
python zeiss_headless_complete_v5.py --command "sequence run scan_A"
```

Note that starting the controller itself loads the microscope configuration, even for informational CLI commands. Use the offline tests to inspect the sequence logic without connecting to hardware:

```bat
python -m unittest -v test_zeiss_headless_complete_v5_compat test_zeiss_headless_complete_v5
```

## Offline validation

Python 3.8 syntax was checked and 33 tests passed, covering v4 backward compatibility, persistence, XYZ motion commands, planned grid coordinates, path distance calculations, CSV export and sequence execution against a mock Core. The v5 sequence features have **not been tested against your physical Zeiss microscope**.
