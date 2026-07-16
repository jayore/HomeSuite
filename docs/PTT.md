# Push-to-Talk (PTT) Guide

Home Suite PTT uses one maintained GPIO input to open and close a microphone
listening session. The control can be a held push-to-talk button, telephone hook,
foot switch, or another switch that remains in one state while Home Suite should
listen. Leaving that state closes the session. Depending on
`PTT_END_BEHAVIOR`, Home Suite either submits captured speech or cancels
capture and stops local assistant speech.

A conventional PTT button normally listens while held and stops on release. A
telephone can listen while its handset remains lifted. Both use the same runtime
path; their wiring, activation state, and end-of-capture behavior are configurable.

PTT and wake-word capture share transcription, interaction policy, phonetic
repairs, and deterministic command routing after audio is captured. Their
trigger timing, VAD endpoint policy, chimes, and rearm behavior remain separate.

## Hardware Contract

The PTT input uses **BCM GPIO 11** by default with the Pi's internal pull-up
enabled. Configure both the pin and the level observed while Home Suite should
listen:

```python
PTT_GPIO_PIN = 11
PTT_LISTEN_LEVEL = "low"
PTT_END_BEHAVIOR = "cancel"
```

`low` is the common choice for a control that connects GPIO to ground while
listening. Use `high` for inverse wiring. GPIO sees only electrical levels, not
mechanical concepts such as pressed, released, lifted, or hung up.

`PTT_END_BEHAVIOR` controls what happens when the GPIO leaves its listening
state during an utterance:

* `submit` ends capture and processes speech already recorded. Use this for a
  conventional hold-to-talk button where release means send.
* `cancel` discards the partial utterance and stops local speech. Use this for a
  telephone hook where hanging up means dismiss or interrupt.

Verify your board numbering before wiring; BCM 11 is not physical header pin
11. A PTT-enabled node fails at startup if `RPi.GPIO` is unavailable. Text/API
and wakeword-only nodes can run without that package.

The systemd service must run as a user that can access GPIO and the selected
audio devices. The native installer uses the account that ran the installer.

## Enable One Device

Configure the PTT device in its ignored `local_prefs.py`:

```python
DEFAULT_ROOM = "office"

PTT_ENABLED = True
WAKEWORD_ENABLED = False
PTT_GPIO_PIN = 11
PTT_LISTEN_LEVEL = "low"
PTT_END_BEHAVIOR = "cancel"

ASSISTANT_AUDIO_OUTPUT_MODE = "local"
START_CHIME_DELAY_SECONDS = 0.0
```

`START_CHIME_DELAY_SECONDS` optionally delays the first cue after PTT activates.
Keep it at zero for a held button. A handset may benefit from a small delay so
the receiver reaches the user's ear. It does not change capture after the cue.

`PTT_ENABLED` is the complete PTT switch. When it is false, Home Suite does not
configure or poll the PTT pin. HTTP, Telegram, scheduler, auxiliary command
buttons, and wake-word components can continue in the same process.

When one node enables both PTT and wake-word listening,
`WAKEWORD_SUPPRESS_WHILE_PTT = True` prevents the detector from opening a second
interaction while the PTT input is active.

PTT and wake-word capabilities are deliberately composable. Enabling one does
not disable the other, and role discovery, diagnostics, and the management
console report both when both are enabled. Wake-word listening resumes after
the PTT session closes; its detector remains available whenever PTT is idle.

## Configure Capture

PTT uses the device's `AUDIO_INPUT_PROFILE`, including stable device matching,
sample rate, hardware mixer enforcement, and `ptt_volume_multiplier`:

```python
AUDIO_INPUT_PROFILE = {
    "name": "office_ptt_mic",
    "device_match": "USB PnP Sound Device",
    "device_index": None,
    "sample_rate": 48000,
    "channels": 1,
    "stream_latency": "high",
    "strict_device_match": True,
    "alsa_card": "Device",
    "mixer_control": "Mic",
    "mixer_value": 7,
    "verify_interval_sec": 15,
    "noise_suppression_level": 0,
    "auto_gain_dbfs": 0,
    "volume_multiplier": 1.0,
    "command_noise_suppression_level": 0,
    "command_auto_gain_dbfs": 0,
    "command_volume_multiplier": 1.0,
    "ptt_volume_multiplier": 1.0,
    "aec_mode": "none",
}
```

PTT currently opens an input stream for each utterance with high PortAudio
latency to provide scheduling headroom on smaller Pis. `PTT_AUDIO_CAPTURE`
reports frames, software gain, and overflow count after every capture. Any
non-zero overflow count means samples were lost before VAD/STT received them.

The management console's **Audio** view displays and edits this profile for a
PTT-only or combined PTT/wake-word node. Its guided calibration temporarily
blocks a new PTT session, measures room noise and normal speech, and returns to
the normal input loop automatically. It does not alter the saved gain.

For headless calibration, stop the service so the CLI owns the microphone:

```bash
sudo systemctl stop homesuite.service
cd ~/homesuite
source .venv/bin/activate
python tools/calibrate_mic.py --list-devices
python tools/calibrate_mic.py --match "USB PnP Sound Device" --show-alsa
```

Normal speech should avoid clipping and produce the calibration tool's healthy
speech recommendation. Prefer hardware gain and microphone placement before
adding `ptt_volume_multiplier`.

## Session Behavior

1. Enter the configured PTT input state; Home Suite resets session state and
   plays the initial cue.
2. VAD captures one utterance and stops after configured consecutive silence.
3. The transcript enters the shared command/interaction pipeline.
4. While PTT remains active, Home Suite waits for response speech to finish and
   then listens for another utterance.
5. Leave the listening state at any point to cancel capture or immediately
   terminate local TTS.

Unlike wakeword mode, PTT does not use rolling wakeword endpointing, a cue guard,
or model rearm. It uses `SILENCE_END_MS`, `PRE_ROLL_MS`, `MIN_SPEECH_MS`,
`MAX_UTTERANCE_SECONDS`, and `VAD_MODE` from shared defaults.

The current hosted STT modes require `OPENAI_API_KEY`. `PIPHONE_STT_MODE` may
select streaming or file behavior, but `whisper` still means OpenAI's hosted
Whisper API rather than a local model.

## Interruption and Cancellation

Leaving the configured PTT listening state is the authoritative interruption
gesture. It stops local speech immediately and prevents an in-flight recording
from becoming a command.

While capture is active, exact `cancel`, `never mind`, or `nevermind`
transcripts dismiss the utterance silently and return to the active PTT listening
loop. Longer phrases such as `cancel my timer` continue to their normal command
handler.

Wakeword barge-in is a separate feature and does not modify PTT behavior.

## Operational Checks

```bash
journalctl -u homesuite.service -f -o cat \
  | grep -E 'PTT_SESSION|PTT_AUDIO_CAPTURE|VAD_|STT_|TRANSCRIPTION_TEXT|ACTION_DECISION'
```

Useful checks:

* `PTT_AUDIO_CAPTURE overflows=0` after repeated utterances
* the configured `AUDIO_INPUT_PROFILE` is selected by stable name
* leaving the PTT listening state during TTS stops playback immediately
* remaining in the listening state permits another command after the response

## Troubleshooting

| Symptom | Check | Likely action |
| --- | --- | --- |
| PTT state is not detected | configured BCM pin and live electrical level | Confirm BCM numbering, wiring, and `PTT_LISTEN_LEVEL`. |
| First words are quiet or clipped | calibration output and mixer value | Adjust hardware gain; avoid compensating only in software. |
| Transcripts lose audio | `PTT_AUDIO_CAPTURE overflows` | Use profile `stream_latency="high"`, stop competing capture, and inspect Pi load. |
| It hears the assistant response | output-to-mic placement and cooldown | Reduce acoustic coupling; hanging up remains immediate interruption. |
| No speech after PTT is released | expected behavior | Leaving the listening state cancels the current utterance by design. |
