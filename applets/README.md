# Experimental Applets

Applets are optional extensions, not part of the portable core runtime or its
role-based acceptance contract. Button modes reuse Home Suite directly;
subprocess applets may need additional packages or exclusive access to local
hardware.

## Note Lights

Note Lights maps instrument pitch to Home Assistant light actions. It needs a
microphone, exclusive capture access while running, and a heavier
librosa/Numba/LLVM dependency stack that the core installer deliberately does
not install.

On the device that will run it:

```bash
cd ~/homesuite
.venv/bin/python -m pip install -r applets/requirements-note-lights.txt
```

Then stop any service currently holding the microphone and test the applet
directly:

```bash
sudo systemctl stop homesuite.service
.venv/bin/python applets/note_lights.py --list-devices
.venv/bin/python applets/note_lights.py --device 1
```

The current 32-bit Raspberry Pi 3 environment is not a supported Note Lights
target because its librosa/Numba/llvmlite stack does not install reliably. A
64-bit Pi 4-class device is the known working target. Core text, PTT, wakeword,
API, and deterministic command support do not depend on this applet.

When dependencies are missing, `run note lights` now returns a short
not-available response without releasing the active microphone or launching a
child process. The exact missing modules and requirements file are recorded in
the Home Suite log.
