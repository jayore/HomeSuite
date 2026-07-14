# Choose a Node Role

Home Suite can run as a quiet text/API node, a physical push-to-talk handset,
or a wakeword appliance. Start with one role on each device. All roles share
the same Home Assistant topology, credentials, command routing, confirmations,
and source-scoped continuity.

Shared home topology belongs in `deployment_config.py`. Credentials belong in
`private_config.py`. One Pi's role, room, microphone, and output settings
belong in its ignored `local_prefs.py`.

## Text and API Node

This is the recommended first install. It supports the authenticated HTTP and
WebSocket API, Telegram, scheduled jobs, and safe local text testing without
any microphone or GPIO hardware.

```python
# local_prefs.py
SOURCE_ID = "office_server"
DEFAULT_ROOM = "office"
HANDSET_PRESENT = False
PTT_ENABLED = False
WAKEWORD_ENABLED = False
UNIFIED_SERVER_ENABLED = True
```

Validate it with:

```bash
homesuite doctor --live
homesuite test "what lights are on?"
```

`homesuite test` reads real Home Assistant state but blocks writes. A planned
service call appears as `HA_STUB call` instead of changing a device.

## Push-to-Talk Handset

Use this role for a handset, hook switch, or a deliberately wired PTT build.
Configure the audio profile by stable microphone name rather than a device
index, then calibrate before relying on transcription.

```python
# local_prefs.py
SOURCE_ID = "office_handset"
DEFAULT_ROOM = "office"
HANDSET_PRESENT = True
PTT_ENABLED = True
WAKEWORD_ENABLED = False
ASSISTANT_AUDIO_OUTPUT_MODE = "local"
```

Then use:

```bash
homesuite doctor --role ptt
homesuite calibrate-mic --list-devices
homesuite calibrate-mic --match "your microphone name" --show-alsa
```

The default active-low hook input is BCM GPIO 11 and can be changed with
`HANDSET_GPIO_PIN`. See the [PTT handset guide](PTT.md) for its wiring
contract, calibration, session behavior, and troubleshooting.

## Wakeword Appliance

Use this role for a continuously listening room device. Install the optional
detector packages only on that node, choose a model, and verify the selected
model at service startup before tuning thresholds.

```bash
homesuite install-wakeword
```

```python
# local_prefs.py
SOURCE_ID = "living_room_voice"
DEFAULT_ROOM = "living_room"
HANDSET_PRESENT = False
PTT_ENABLED = False
WAKEWORD_ENABLED = True
WAKEWORD_ENGINE = "openwakeword"
WAKEWORD_MODEL = "your_model_label"
WAKEWORD_MODEL_PATHS = ["/home/your-user/wake_models/your_model.onnx"]
```

Then run:

```bash
homesuite doctor --role wakeword
homesuite calibrate-mic --match "your microphone name" --show-alsa
homesuite wakeword-lab capture --mode positive --phrase "your wake phrase"
```

The [wakeword guide](WAKEWORD.md) covers model verification, one-breath
capture, calibration, threshold replay, rearm, and barge-in.

## Combined Hardware

PTT and wakeword are intentionally isolated after their audio enters the
shared interaction pipeline. A combined device is supported, but set up and
validate each path independently first. Do not use a successful PTT test as
evidence that wakeword microphone placement, model loading, or echo behavior is
ready.

## What Doctor Reports

Run `homesuite doctor` after each configuration change. It reports readiness
for the roles enabled in `local_prefs.py`; `--role text`, `--role api`,
`--role ptt`, or `--role wakeword` can validate one path explicitly. Add
`--live` to check Home Assistant reachability, configured entity IDs, and a
running local API listener.

For the short, repeatable path from setup to a live device, continue with the
[acceptance checklist](ACCEPTANCE.md).
