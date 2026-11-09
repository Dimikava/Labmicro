# Zeiss Axio Observer Z1 — Headless Controller v6

**Goal:** Extend the tested v5 headless manual controller with **PixeLINK camera capture** and a persistent, resumable XYZ experiment orchestrator.

**Target environment**: Windows 7 Ultimate SP1 x64; Python 3.8.10 (`mm38` virtual environment); existing and previously tested `pycromanager==1.0.2` Java backend with the Python 3.8 `mmpycorex` compatibility patch; Micro-Manager 2.0; ZeissCAN29 via COM6 at 57600 baud. **Do not install a second Micro-Manager or change existing DLLs.**

The Java backend and Z stages have worked on your actual microscope. **The new v6 camera, run-control and image-writing code has only passed offline mock tests**; verify it on a stationary sample first.

## Files

- `zeiss_headless_complete_v6.py`: **main v6 program**.
- `zeiss_headless_complete_v5.py`: v5 library, imported by v6 to retain all working manual controls, homes and sequence definitions.
- `zeiss_camera.cfg`: **snapshot copied from your earlier `complete.cfg`**. Includes PixeLINK camera `422000035`, SerialManager COM6, ZeissScope, all earlier Zeiss devices, and `Property,Core,Camera,422000035`. **Replace this copy with an updated `.cfg` exported from your current working Micro-Manager 2.0 GUI if the hardware configuration has changed.** Do NOT overwrite the config used by the GUI.
- `test_zeiss_headless_complete_v6.py`: offline tests, **does not connect to hardware**.
- `start_zeiss_v6.bat`: Windows 7 launcher.

Keep the following pre-existing files alongside the v6 files (if you have them):

- `zeiss_user_homes.json`: saved named home coordinates from v4/v5.
- `zeiss_sequences.json`: saved v5 XYZ sequences.

Do not delete or overwrite these two JSON files when upgrading. v6 reads them directly.

## Setup

Copy/extract the ZIP to `D:\Users\zeiss\Desktop\microGod`. From Command Prompt:

```bat
cd /d D:\Users\zeiss\Desktop\microGod
mm38\Scripts\activate
python -m unittest -v test_zeiss_headless_complete_v6
```

The offline tests require **NumPy**, already present in your previous diagnostic (`numpy==1.24.4`). They do not require Micro-Manager.

If your camera configuration has been updated since the original file you shared: in the **working Micro-Manager GUI**, save a fresh hardware configuration as `zeiss_camera.cfg` into `microGod`, retaining its functional Zeiss and camera devices. Then **close the GUI** and any other owner of COM6 or the PixeLINK camera.

Start v6:

```bat
python zeiss_headless_complete_v6.py
```

If you saved the combined configuration elsewhere, override the path:

```bat
python zeiss_headless_complete_v6.py --config "D:\Users\zeiss\Desktop\my_working_camera.cfg"
```

The CLI does **not** require new packages for `.npy` acquisition. For optional per-frame TIFF output on Python 3.8:

```bat
python -m pip install "tifffile==2023.4.12"
```

Use the pinned 2023.4.12 version: later releases dropped Python 3.8 support. `npy` is the default lossless format and remains available without this package. `.tif` output is **individual TIFF files, not a single OME-TIFF stack**.

## Phase A — Camera-first test (no stage commands)

At the interactive `zeiss>` prompt:

```text
camera info
camera snap D:/MicroscopeData/snap_test.npy
```

If TIFF support was installed:

```text
camera snap D:/MicroscopeData/snap_test.tif
```

This **does acquire an image** and may actuate an auto-shutter if configured, but it sends no explicit XYZ stage move. Camera and microscope still initialize when the config is loaded. `camera info` should show the PixeLINK camera, width, height, pixel depth, exposure and number of components.

You can inspect the `.npy` file in Python:

```python
import numpy as np
image = np.load(r'D:\MicroscopeData\snap_test.npy')
print(image.shape, image.dtype, image.min(), image.max())
```

`camera snap` also writes a `.json` sidecar containing camera metadata. Data are **not rescaled to 8-bit**. For 32-bit packed RGB returned as BGRA on little-endian Windows, the program converts channels to RGBA and records that change in metadata. Unsupported camera buffer formats fail explicitly without saving a potentially corrupted image.

## Phase B — One-position acquisition

Choose a named saved XYZ home only after checking that it represents the intended current physical region:

```text
mark-home test_A
sequence create single test_A 0 0 0 0 0 0
experiment create first_test single D:/MicroscopeData
experiment show first_test
experiment preview first_test 1
experiment run first_test
experiment status
```

This should acquire **one** frame. `mark-home` stores the current XYZ readouts; no stage moves are commanded until `experiment run`.

## Phase C — Small raster (2 positions)

```text
sequence create small_scan test_A 1 0 0 1 0 0
experiment create scan_test small_scan D:/MicroscopeData
experiment exposure scan_test 15
experiment dwell scan_test 100
experiment preview scan_test 2
experiment run scan_test
experiment status
```

`sequence create` uses v5's **intervals** convention: 1 X interval = 2 positions. Coordinates and distances are in **micrometers**; exposures and dwell periods are in milliseconds. The 1 µm travel, 15 ms exposure and 100 ms dwell are illustrative, **not stage or camera recommendations**. Only run them where appropriate for the actual microscope and specimen.

Each experimental run creates a **new directory**, e.g.:

```text
D:/MicroscopeData/scan_test/20261108_180201_scan_test/
    plan.json              # frozen experiment recipe + frozen XYZ plan
    manifest.json          # progress and committed frame indices
    events.jsonl           # timestamped log
    frames.csv             # target and actual XYZ, image name, exposure, timestamp
    frames/
        frame_000000.npy   # lossless full-resolution pixel array
        frame_000000.json  # individual frame metadata including optical states
        frame_000001.npy
        frame_000001.json
```

Every successfully saved frame is committed to the manifest. Data from earlier runs are not overwritten by `experiment run`.

## Phase D — Optical settings per XYZ point

Actions execute in this order by default: **move XYZ → wait for stage and focus → dwell → snap image → save image**. Additional actions are inserted *before* the snap. The following are optional examples; use labels/properties that actually exist in your `.cfg`:

```text
experiment add-state scan_test reflector 1
experiment add-label scan_test side "1-100%L"
experiment add-property scan_test ZeissHalogenLamp "<EXACT PROPERTY NAME>" "<VALUE>"
experiment add-preset scan_test GROUP PRESET
experiment show scan_test
```

`experiment exposure scan_test 15` sets camera exposure before each image. Optical device changes wait for their corresponding device, and presets wait for system idle. You can edit `zeiss_experiments.json` between runs to customize the validated `actions` list. **Do not edit the file while an experiment is running.**

To remove optional actions:

```text
experiment clear-extra scan_test
```

## Pause, cancel and recovery

`experiment run` launches one background worker and returns the console prompt. Only job-control commands are available while acquisition is active; this prevents two threads from independently commanding the same microscope devices.

```text
experiment status
experiment pause
experiment resume
experiment cancel
```

Pause and cancellation are **cooperative**: they take effect at the next action checkpoint. They cannot interrupt a blocking camera read or a currently executing hardware command, and **are not hardware emergency stops**.

To continue an interrupted or cancelled acquisition in a *new process*:

```text
experiment continue "D:/MicroscopeData/scan_test/20261108_180201_scan_test"
```

Replace that example directory with an actual run folder. v6 reuses its frozen `plan.json`, checks all previously committed image and metadata files, skips them, and resumes with the next uncommitted position. If any committed file is missing, it stops and reports the problem rather than silently overwriting data.

A run's `.json` and `.npy`/`.tif` files are independent; `frames.csv` is rebuilt from frame metadata on resume and completion. After power cycling, check that saved absolute home coordinates still describe the intended physical positions before resuming.

For a noninteractive run:

```bat
python zeiss_headless_complete_v6.py --command "experiment run scan_test"
```

The process waits for that acquisition to finish before shutting down the Java backend.

## Backwards compatibility

All existing **v5 manual commands** remain accessible (Z/XY movement, turrets, lamp, named XYZ homes, and `sequence` commands). Use `help` for v5 commands and `v6-help` for camera/experiment commands. Original `zeiss_only.cfg` and `zeiss_headless_complete_v5.py` remain available independently.

**Validation status:** v6: 15 offline unit tests passed with a fake Java Core and camera; legacy v5: 33 tests passed unchanged. **Not yet physically tested with PixeLINK/Zeiss.** The v6 acquisition runs sequentially at software timing, not synchronized DAQ/galvo triggering. No software travel bounds or per-step confirmation prompts are added; hardware limits and laboratory practice remain essential.
