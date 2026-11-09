ZEISS HEADLESS READ-ONLY CONSOLE
================================

Requirements: Windows 7 SP1 x64, Python 3.8.10 virtual environment mm38,
Micro-Manager 2.0 and a working pycromanager Java backend.

This is a continuation of the successful headless_zeiss_java_v2.py full
configuration test. Copy zeiss_headless_console.py next to zeiss_only.cfg in:
D:\Users\zeiss\Desktop\microGod\

Open Command Prompt:
  cd /d D:\Users\zeiss\Desktop\microGod
  mm38\Scripts\activate
  python zeiss_headless_console.py

Type YES to initialize the microscope. Other programs accessing COM6 should
be closed. The script is READ-ONLY after initialization, but loading the
configuration itself may change hardware state.

Commands:
  devices
  status
  z
  xy
  states
  properties ZeissHalogenLamp
  properties ZeissObjectiveTurret
  property COM6 BaudRate
  info ZeissFocusAxis
  version
  quit

One-shot status (still requires YES confirmation):
  python zeiss_headless_console.py --command status

Alternate Micro-Manager directory or config:
  python zeiss_headless_console.py --mm-dir "C:\Program Files\Micro-Manager-2.0" --config zeiss_only.cfg

No motor, light-path, lamp, home, stage, shutter or camera write commands are
implemented. The file is not hardware-validated and should not be used to
assume the device position/status reports are correct without verification.
