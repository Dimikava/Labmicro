ZEISS HEADLESS DIRECT CONTROL v2
===============================
Target: Windows 7 Ultimate SP1 x64, Python 3.8.10, the already tested
Micro-Manager 2.0 installation and Pycro-Manager Java headless backend.

CONTENTS
  zeiss_headless_control_v2.py   Interactive controller; direct writes
  zeiss_only.cfg                Your tested microscope-only configuration
  test_zeiss_headless_control_v2.py   Offline mock tests

Install / run
-------------
1. Copy the script and zeiss_only.cfg to:
       D:\Users\zeiss\Desktop\microGod
2. Keep the existing mm38 environment with the previously tested
   Python 3.8 compatibility patch. No extra pip installs required.
3. Close Micro-Manager GUI, LabVIEW and other clients using COM6.
4. Run:
       cd /d D:\Users\zeiss\Desktop\microGod
       mm38\Scripts\activate
       python zeiss_headless_control_v2.py
5. At the zeiss> prompt, type help to list commands.

Example direct commands (use only values appropriate for your hardware):
   status
   labels ZeissObjectiveTurret
   properties ZeissHalogenLamp
   jog-z 0.5
   jog-xy 2 -2
   set-state ZeissReflectorTurret 2
   set-label ZeissSidePort "1-100%L"
   wait ZeissFocusAxis
   stop ZeissXYStage
   quit

For one-shot commands:
  python zeiss_headless_control_v2.py --command "status"
  python zeiss_headless_control_v2.py --command "jog-z 0.5"

This version intentionally removes the v1 software position bounds,
maximum displacement checks and per-command confirmation prompts.
Only explicit commands issued at the prompt trigger control actions;
there is no automatic movement sequence after startup. Initializing the
Micro-Manager config itself may change hardware state.

IMPORTANT
- Commands are sent directly; physical collision/interlock protection
  depends entirely on the microscope hardware and its existing controllers.
- Units for XY and Z are micrometers, as reported by MMCore.
- Stage zero and direction are dictated by your actual hardware/configuration.
- For turret state indices, first list labels for the specific device.
- Changing an objective can collide with sample/stage. Do not assume
  software state labels reflect physical objective identity without checking.
- Generic properties are NOT all writable. For the lamp, first inspect
  its exposed properties; there is no hardcoded intensity property name.
- A write acknowledged by MMCore does NOT guarantee completion; verify
  with status, z, xy, state, or wait DEVICE. wait can block until timeout.
- The PixeLINK camera is not included.
- Do not replace any working Micro-Manager DLLs or serial drivers.

Tests (no hardware):
  python -m unittest -v test_zeiss_headless_control_v2.py

This package is tested with an offline fake core only. It has not been
executed against your physical microscope. Its MMCore methods are
based on documented Java CMMCore API names used through Pycro-Manager.
