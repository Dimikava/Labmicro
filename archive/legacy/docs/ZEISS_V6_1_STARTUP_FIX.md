# Zeiss Headless v6.1 — Startup path fix

For the existing Windows 7 SP1 x64, Python 3.8 virtual environment (`mm38`), Micro-Manager 2.0 and ZeissCAN29/COM6 installation.

## Diagnosis

Your screenshot shows v6 fails in `start_headless`, but the same computer starts v5 with 12 loaded devices. V6's `--config zeiss_only.cfg` was passed as a relative string; v5's default is a full absolute path. Because headless Micro-Manager starts in a separate Java process, an absolute path is more reliable. The initial v6 default `zeiss_camera.cfg` was also taken from an older snapshot and may not match your now-working camera configuration.

## Change in v6.1

`zeiss_headless_complete_v6_1.py` resolves `--config` to an absolute path **before starting Java**, prints the actual path, counts devices, and reports if a PixeLINK camera is declared and assigned to Core.Camera. It leaves Zeiss movement commands, saved homes, sequence definitions, and experiment logic unchanged. It does not modify Micro-Manager, DLLs or Python dependencies.

## Installation

Extract the ZIP into your existing folder:

    D:\Users\zeiss\Desktop\microGod

Keep your currently working `zeiss_only.cfg`, any updated combined-camera config, and these persistent files:

    zeiss_user_homes.json
    zeiss_sequences.json
    zeiss_experiments.json

Close the Micro-Manager GUI and any other program using COM6 or the PixeLINK camera. From the `(mm38)` command prompt:

    cd /d D:\Users\zeiss\Desktop\microGod
    python zeiss_headless_complete_v6_1.py --config zeiss_only.cfg --debug

Expect a line such as:

    Configuration (absolute): D:\Users\zeiss\Desktop\microGod\zeiss_only.cfg

The Zeiss-only configuration will **not enable camera acquisition**; it tests v6.1 startup using the known-working hardware subset. If it succeeds, type `status` and `quit`.

## Then test the updated camera configuration

Open the Micro-Manager GUI with your working Zeiss + PixeLINK setup. Save a fresh hardware configuration, for example:

    D:\Users\zeiss\Desktop\microGod\zeiss_camera_working.cfg

Close the GUI, and run:

    python zeiss_headless_complete_v6_1.py --config zeiss_camera_working.cfg --debug

Look for:

    PixeLINK declaration: 422000035
    Core camera role: 422000035

If startup succeeds, use:

    camera info
    camera snap D:/MicroscopeData/test.npy

The camera name is only an example based on the earlier `complete.cfg`; use the actual label in your exported file.

## If startup still fails

Check whether the **v5 controller** loads the *same* combined-camera configuration, as an independent comparison:

    python zeiss_headless_complete_v5.py --config zeiss_camera_working.cfg --command status --debug

If v5 fails with the combined-camera config but succeeds with `zeiss_only.cfg`, investigate the camera adapter's initialization, dependencies and device-driver availability in the Micro-Manager CoreLog. If v5 succeeds and v6.1 fails using the exact same absolute config, send both full console outputs.

To capture the output:

    python zeiss_headless_complete_v6_1.py --config zeiss_camera_working.cfg --debug > v6_1_debug.txt 2>&1

Do not reinstall Micro-Manager or change the working CH341 driver based on this error alone.

## Offline tests

    python -m unittest -q test_v6_1_paths

This never contacts the microscope.
