# Zeiss Headless Microscope Controller v7

**Target:** Zeiss Axio Observer Z1 + PixeLINK camera, Micro-Manager 2.0 headless Java backend, Windows 7 Ultimate SP1 x64, Python 3.8.10.

This package extends the **already working** v5 manual controller and v6.1 camera snap code. It does not change the Micro-Manager installation, CH341 driver, Java libraries, device adapters, or hardware configuration.

## What's new

The XYZ scan is **sequential home → step → exact endpoint**, not the older v5 “N intervals + distance” format. For each axis:

- Start = X/Y/Z from the saved named home.
- End = an **absolute** stage coordinate in µm, set separately for X, Y and Z.
- Step = a **positive distance magnitude** in µm; the controller determines positive/negative travel direction from start and end. A negative input is normalized to its magnitude.
- Both endpoints are included. If the distance does not divide evenly, the **last step is shorter**. Example: home X=10, step=2, end X=15 → 10,12,14,15.
- X runs fastest; Y is the next axis; Z planes run outermost. `serpentine` (default) reverses alternate X rows, while `raster` resets to the first X coordinate for each row.
- Disable an axis by setting its endpoint equal to its home coordinate.
- The home coordinates are snapshotted in the experiment recipe and **frozen again in each run**. Editing a home later does not silently move an old experiment. Use `experiment refresh-home NAME HOME` to opt into an updated origin.

### Included functions

- Existing direct Z, XY, reflector, objective, port, tube lens, excitation, halogen and generic Core commands (v5)
- Existing camera information and single-image snaps (v6.1)
- Multiple independent home regions, each with their own X/Y/Z step and endpoints
- XY serpentine or raster, Z-stack, multi-position, time-lapse, multiple channels, repeated frames
- Per-channel exposure and optical presets / state / property changes
- Pixel-size-derived **approximate** overlap step helper for axis-aligned, calibrated imaging
- Preview, validation, CSV plan export, start, progress, pause, resume, cancel and restart from a frozen run
- Lossless `.npy`, TIFF, and **single-frame grayscale** OME-TIFF output (using `tifffile`)
- Per-image intended and measured XYZ, camera settings, optical state, UTC timestamp, checksum and experiment context
- Atomic per-image/per-metadata writes; manifest checkpoint; detection of missing/corrupt committed frames
- Legacy v5 sequence/XYZ home files remain intact; v7 recipes live in `zeiss_experiments_v7.json`.

## Installation (do not overwrite the working Micro-Manager configuration)

1. **Back up your `microGod` folder.** Extract this ZIP into `D:\Users\zeiss\Desktop\microGod`. It contains v5 and v6.1 Python source dependencies, v7, tests and a reference `zeiss_only.cfg`. You can keep old v5/v6 files under their existing names; this package imports them.
2. Keep your current **working Zeiss + PixeLINK configuration** at `D:\Users\zeiss\Desktop\microGod\zeiss_camera_working.cfg`. It is **not** included in this ZIP because it must be the configuration that actually works on your PC.
3. Keep the working Python 3.8 environment at `microGod\mm38`, including the previous local `mmpycorex` Python 3.8 compatibility patch. Do not reinstall Pycro-Manager or Micro-Manager.
4. Close the **Micro-Manager GUI**, PixeLINK Capture and any process currently using the microscope or camera. The GUI and headless backend cannot safely share their hardware connection.
5. In Command Prompt:

```cmd
cd /d D:\Users\zeiss\Desktop\microGod
mm38\Scripts\activate
python zeiss_headless_complete_v7.py --config zeiss_camera_working.cfg
```

Alternatively double-click `start_zeiss_v7.bat`. With `--debug`, Java headless startup prints more detail.

Type `v7-help` for all experiment commands and `help` for the original direct controls.

**Optional TIFF support** (only if not already installed):

```cmd
python -m pip install "tifffile==2023.4.12"
```

NumPy and Pycro-Manager come from your previously working `mm38` environment. No new dependency is required for lossless NPY files or the v7 planner.

## First integrated 2 × 2 XY scan (4 images, no Z stepping)

Move the stage to a convenient starting position using the already working direct controls, and capture it as a named home:

```text
zeiss> mark-home tile_origin
zeiss> experiment create xy_test tile_origin D:/MicroscopeData
zeiss> experiment offset xy_test x 2 2
zeiss> experiment offset xy_test y 2 2
zeiss> experiment preview xy_test 10
zeiss> experiment validate xy_test
zeiss> experiment run xy_test
```

This uses home and end offsets: X=homeX then homeX+2 µm, Y=homeY then homeY+2 µm, and Z stays at homeZ. **Four frames**, with X changing fastest. Preview/validate do not command stage movement. `experiment run` begins immediately (no additional guardrails or per-move confirmation), so use a sample and objective position appropriate for the test.

### Absolute endpoint scan (the requested XYZ model)

Suppose your recorded home is X=100, Y=200, Z=50 µm and you want:

- X ending at 106 µm, stepping 2 µm
- Y ending at 204 µm, stepping 2 µm
- Z ending at 52 µm, stepping 1 µm

```text
zeiss> experiment create volume_A sample_A D:/MicroscopeData
zeiss> experiment axis volume_A x 2 106
zeiss> experiment axis volume_A y 2 204
zeiss> experiment axis volume_A z 1 52
zeiss> experiment preview volume_A 15
zeiss> experiment run volume_A
```

This produces 4 × 3 × 3 = **36 images** per channel/time point. For any other recorded home, change the absolute endpoints to the correct coordinates. The numbers are illustrative, not travel recommendations.

For a scan ending *behind* the saved origin, specify a lower end coordinate; the direction is inferred. For convenient relative endpoints: `experiment offset NAME x STEP SIGNED_OFFSET`.

## Multi-region, multi-channel, time-lapse

```text
zeiss> mark-home sample_B
zeiss> experiment add-region volume_A sample_B
zeiss> experiment offset volume_A x 2 10 sample_B
zeiss> experiment offset volume_A y 2 6 sample_B
zeiss> experiment offset volume_A z 1 4 sample_B
zeiss> experiment channel volume_A brightfield
zeiss> experiment exposure volume_A 15 brightfield
zeiss> experiment channel volume_A fluorescence
zeiss> experiment exposure volume_A 80 fluorescence
zeiss> experiment channel-state volume_A fluorescence reflector 2
zeiss> experiment time volume_A 3 60
zeiss> experiment repeats volume_A 2
zeiss> experiment format volume_A tiff
zeiss> experiment show volume_A
zeiss> experiment validate volume_A
zeiss> experiment run volume_A
```

Time lapse `3 60` means three timepoints, with nominal **60 seconds between the starts** of successive timepoints. If a scan takes longer than the requested interval, the next timepoint begins late; timing overruns are recorded in `events.jsonl`. Timepoints are **not** hard real-time synchronized.

The `channel-state` command uses an example reflector position. Verify appropriate states using `labels reflector` before using them. Exposure values are in milliseconds.

## Additional experiment commands

```text
experiment list
experiment show volume_A
experiment preview volume_A 20
experiment export volume_A D:/MicroscopeData/volume_A_plan.csv
experiment pattern volume_A raster
experiment dwell volume_A 25
experiment return-home volume_A off
experiment status
experiment pause
experiment resume
experiment cancel
experiment runs volume_A
experiment continue "D:/MicroscopeData/volume_A/20261108_190000_run"
```

Use the **actual run folder** printed by the controller for `experiment continue`; the above folder is only an example. Resume uses the immutable `plan.json`, validates checksums for committed images, and never guesses an altered home/endpoint.

Channel actions available: `channel-preset`, `channel-state`, `channel-label`, `channel-property`; `clear-channel` removes a channel's accumulated optical actions. `remove-channel` and `remove-region` delete from the recipe (not existing files).

## Camera field-of-view / overlap option

For a properly calibrated, axis-aligned camera/stage mapping:

```text
zeiss> experiment overlap volume_A 10 10
```

This sets X/Y step magnitudes to `width_px * pixel_size_um * 0.90` and `height_px * pixel_size_um * 0.90`, while **retaining the previously specified end coordinates**. Use only after calibrating pixel size, camera rotation, and stage orientation. This is a geometric estimate, not a validated image-stitching transform. If Micro-Manager returns pixel size `0`, the command stops without changing the experiment.

## Dataset contents

```
D:/MicroscopeData/
  volume_A/
    YYYYMMDD_HHMMSS_run/
      plan.json                    frozen experiment, home XYZ, steps, endpoints
      manifest.json                status, committed frame indices, failure reason
      events.jsonl                 timepoint timing, saved frames, errors, resumes
      frames.csv                   indexed frame summary
      images/
        P00_T000_C00_Z0000_Y0000_X0000_Q00.npy
        ...
      metadata/
        frame_00000000.json       raw image SHA-256; commanded and measured XYZ
        ...
```

File names encode region (P), time (T), channel (C), Z/Y/X grid index, repeated frame (Q). Images preserve original bit depth and values. Captured frames are saved individually, not automatically stitched or reprojected. OME-TIFF is one OME-TIFF per 2D grayscale frame, **not a single combined 5D OME-TIFF**.

Image metadata includes camera identity, dimensions, dtype, exposure, pixel size when calibrated, settings where available, current optical states where readable, commanded and measured XYZ coordinates, UTC timestamp, and checksum. The run plan records software acquisition ordering and complete channel actions.

## Execution, errors, and limitations

- Software **step-and-shoot**: move XY, wait, move Z, wait, apply channel, optional dwell, snap, save. No hardware-triggered galvo/PMT synchronization or enforced exposure-stage interlock beyond device waits.
- V7 intentionally has **no extra software travel boundaries, arming toggles or per-command confirmations**, per the requested direct-control design. Hardware protection is determined by the microscope and device drivers. Avoid unsuitable stage travel or objective changes during an experiment.
- Pause/cancel are cooperative and execute after a currently blocking hardware operation completes, not instant motor emergency-stop operations. `stop stage`/`stop focus` remain available as original manual commands when no experiment is active.
- One acquisition worker at a time. Console blocks manual hardware-changing commands while acquisition is active. Camera and microscope must be owned by the headless backend exclusively.
- When interrupted after image write but before metadata, the existing file is left untouched and recovery stops for inspection; it is not silently overwritten.
- Camera model must support one of the raw pixel types handled by the already working v6.1 `image_array` code (e.g. Gray8/Gray16/Float32/RGBA8); other formats fail clearly rather than truncating data.
- At startup, Micro-Manager initialization may change device state. V7 does not issue any **extra** automatic movement beyond the normal hardware configuration loading; experiments start moving only when `experiment run` is entered.
- Windows 7 and Python 3.8 are end-of-life. Keep the acquisition PC isolated from unnecessary networking.

## Offline validation

From the package root, using a Python environment that has NumPy:

```cmd
python -m unittest discover -s tests -v
```

This exercises the scan planner, store, CLI, mock PixeLINK camera, stage order, metadata, restart/recovery and optional OME output without opening the microscope. It is **not** a substitute for physical hardware tests.

The first physical integration test should be the four-image `xy_test`, followed by a small Z-stack, then multiple regions/channels/time points. This is one software release, not separate prototypes.
