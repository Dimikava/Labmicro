ZEISS HEADLESS CONTROL - STAGE 3 (EXPERIMENTAL)
===============================================
Target: Windows 7 SP1 64-bit, Python 3.8 in the working mm38 environment,
Micro-Manager 2.0 with the same Java backend that already passed the prior test.

FILES
  zeiss_headless_control_v1.py     Interactive controller, read-only by default
  zeiss_only.cfg                  Zeiss configuration from your complete.cfg
  test_zeiss_control_offline.py   Mock-only safety tests (no hardware)

INSTALL
  1. Copy zeiss_headless_control_v1.py and zeiss_only.cfg to:
       D:\Users\zeiss\Desktop\microGod
  2. Leave your previously working virtual environment and pycromanager
     installation exactly as they are. Do NOT replace Micro-Manager DLLs.

RUN READ-ONLY MODE (recommended first)
  cd /d D:\Users\zeiss\Desktop\microGod
  mm38\Scripts\activate
  python zeiss_headless_control_v1.py

  Type YES when prompted; initialization can cause device state changes.
  At the 'zeiss>' prompt:
    devices
    status
    z
    xy
    states
    limits
    properties ZeissFocusAxis
    properties ZeissXYStage
    help
    quit

  Micro-Manager GUI and LabVIEW should be closed before connecting.

OPTIONAL STAGE MOTION - ONLY AFTER VERIFYING CLEARANCE AND TRUE LIMITS
  Any move requires THREE distinct conditions:
    (a) --enable-motion passed at startup
    (b) explicit user-provided Z and/or XY numerical software limits
    (c) a typed MOVE confirmation for every requested movement

  Real-stage bounds are NOT known from the configuration file. Never infer
  bounds from the position readout or the sample chamber dimensions. Obtain
  safe travel limits and collision clearance from the hardware operator,
  microscope setup and hardware manuals first.

  Z-only example SYNTAX with placeholder values (DO NOT RUN LITERALLY):
    python zeiss_headless_control_v1.py --enable-motion --z-limits <MIN_UM> <MAX_UM>

  XY-only example SYNTAX with placeholder values (DO NOT RUN LITERALLY):
    python zeiss_headless_control_v1.py --enable-motion --xy-limits <XMIN_UM> <XMAX_UM> <YMIN_UM> <YMAX_UM>

  With properly verified limits, the console commands are:
    move-z <absolute-Z-um>
    jog-z <delta-Z-um>
    move-xy <absolute-X-um> <absolute-Y-um>
    jog-xy <delta-X-um> <delta-Y-um>

  Maximum allowed single-command displacements default to 1 um in Z
  and 10 um total XY distance. They do not guarantee physical safety.
  The controller does not automatically change turrets, lamp, shutters, or
  the other Zeiss devices. It does not establish safe interlocks and should
  not be used unattended to move a microscope.

  After a motion command, confirm the actual position with 'z' or 'xy'.
  A command being submitted is NOT a guarantee of physical completion.

OFFLINE TESTS (no microscope)
  python -m unittest -v test_zeiss_control_offline.py

TROUBLESHOOTING
  - Use the same mm38 environment as the previously successful Java test.
  - Keep original zeiss_only.cfg and working MM2 installation unchanged.
  - Python pymmcore API 71 cannot load your API 75 device-adapter bundle.
  - This controller uses the Pycro-Manager Java backend, not pymmcore.
  - The PixeLINK camera remains outside this configuration.

NOTE
  Tested offline for syntax and motion checks with a fake MMCore. Not tested
  against your physical microscope. Motion limits are software checks only.
