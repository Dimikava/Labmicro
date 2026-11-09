# Zeiss Axio Observer Z1 + PixeLINK Controller

This is the supported v7 controller for the laboratory microscope computer:

- Windows 7 Ultimate SP1 x64
- Python 3.8.10 from the existing `mm38` directory
- Micro-Manager 2.0 Java-headless backend through Pycro-Manager
- Zeiss Axio Observer Z1 and PixeLINK camera

The project has been consolidated without changing the established hardware
communication. The controller still calls `start_headless` with
`python_backend=False`, obtains `Core()`, uses the existing Micro-Manager
configuration, and stops the Java backend during shutdown.

## Run on the microscope computer

Close Micro-Manager, PixeLINK Capture, LabVIEW, and any other program that may
own the microscope, camera, or COM6. Then double-click:

```text
start_zeiss_v7.bat
```

Equivalent command:

```bat
cd /d D:\Users\zeiss\Desktop\microGod
mm38\Scripts\python.exe zeiss_headless_complete_v7.py --config zeiss_camera_working.cfg
```

Use `help` for direct microscope controls and `v7-help` for acquisition
commands. Existing command names, recipes, homes, manifests, image names, and
resume behavior remain compatible.

Do not rebuild or upgrade `mm38`, Pycro-Manager, Java, Micro-Manager, device
adapters, or drivers as part of normal controller use. The environment contains
the laboratory computer's existing Python 3.8 compatibility patch.

## Supported project layout

- `start_zeiss_v7.bat` — primary laboratory launcher.
- `zeiss_headless_complete_v7.py` — stable Python entrypoint.
- `zeiss_v7/` — canonical manual-control, camera, planning, acquisition,
  storage, console, and Java-backend code.
- `zeiss_headless_complete_v5.py` and `zeiss_headless_complete_v6_1.py` —
  compatibility import/launch wrappers.
- `zeiss_camera_working.cfg` — current combined microscope/camera configuration.
- `zeiss_user_homes.json` and `zeiss_experiments_v7.json` — operator state.
- `tests/` — active offline regression suite.
- `archive/legacy/` — historical implementations and documentation; it is not
  imported or launched by v7.

The remaining `.cfg` files are retained for diagnosis and compatibility. Do not
replace the known-working combined configuration merely to tidy the directory.

## First attended acquisition

Move the stage to a safe starting position using the established manual
controls, then record that XYZ position and create a two-by-two scan:

```text
zeiss> mark-home tile_origin
zeiss> experiment create xy_test tile_origin D:/MicroscopeData
zeiss> experiment offset xy_test x 2 2
zeiss> experiment offset xy_test y 2 2
zeiss> experiment preview xy_test 10
zeiss> experiment validate xy_test
zeiss> experiment run xy_test
```

This produces four images with X changing fastest and no Z stepping. Preview
and validation do not move the microscope. `experiment run` begins movement
immediately and does not add a confirmation prompt.

The v7 coordinate model is **saved home → positive step magnitude → exact
endpoint**. Both endpoints are included; if a distance is not evenly divisible,
the last step is shorter. Direction is inferred from the endpoint. For example:

```text
zeiss> experiment axis xy_test x 2 106
zeiss> experiment axis xy_test y 2 204
zeiss> experiment axis xy_test z 1 52
```

Use `experiment offset NAME AXIS STEP SIGNED_OFFSET` when a relative endpoint
is more convenient. Existing recipes freeze their saved home coordinates; use
`experiment refresh-home NAME HOME` only when intentionally updating a recipe.

Useful run controls:

```text
zeiss> experiment status
zeiss> experiment pause
zeiss> experiment resume
zeiss> experiment cancel
zeiss> experiment runs xy_test
zeiss> experiment continue "D:/MicroscopeData/xy_test/<run-folder>"
```

Pause and cancel are cooperative and take effect after the current blocking
hardware operation. Resume uses the run's frozen plan and verifies committed
image checksums. It does not guess altered coordinates or silently overwrite a
damaged frame.

Each run contains `plan.json`, `manifest.json`, `events.jsonl`, `frames.csv`,
an `images/` directory, and per-frame JSON under `metadata/`. Image and metadata
writes are atomic. TIFF and NPY retain the captured pixel values; v7 OME-TIFF
output is one grayscale 2D OME-TIFF per frame, not a combined 5D file.

## Offline development and verification

The copied `mm38` environment is machine-bound and may not run on another PC.
Use an available Python installation with NumPy for offline tests:

```bat
python -m unittest discover -s tests -v
python -m compileall -q zeiss_v7 zeiss_headless_complete_v5.py zeiss_headless_complete_v6_1.py zeiss_headless_complete_v7.py
```

These tests do not open microscope hardware. Expected traceback text appears in
some recovery tests because those tests deliberately simulate camera and
checksum failures.

An offline experiment preview can be run without loading Micro-Manager:

```bat
python zeiss_headless_complete_v7.py --offline-plan xy_test
```

## Hardware validation after deployment

1. Back up the complete working `microGod` folder.
2. Start and quit the controller without issuing movement commands.
3. Run `status`, `devices`, and `camera info`.
4. Capture one image with `camera snap D:/MicroscopeData/smoke.npy`.
5. Exercise only previously verified manual controls, one at a time.
6. Run the documented four-frame `xy_test` on a safe sample.
7. Inspect its images, metadata, manifest, pause/cancel, and resume behavior.

The software does not add travel limits, arming, or per-movement confirmations.
Physical validation must therefore be attended by a trained operator.

## Rollback

If the consolidated controller fails a hardware smoke test, restore the complete
folder backup. Do not change Micro-Manager, Java, drivers, the patched Python
environment, or `.cfg` contents while deciding whether the consolidation is at
fault. Historical source is preserved under `archive/legacy/` for comparison,
but it is not a drop-in replacement for a complete folder backup.
