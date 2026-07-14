# Push-to-Talk Handset Guide

Home Suite's PTT path is designed for a physical handset or active-low switch.
Lifting the handset opens a multi-utterance session; hanging up cancels capture,
stops local assistant speech immediately, and closes the session.

PTT and wake-word capture share transcription, interaction policy, phonetic
repairs, and deterministic command routing after audio is captured. Their
trigger timing, VAD endpoint policy, chimes, and rearm behavior remain separate.

## Hardware Contract

The handset hook input uses **BCM GPIO 11** by default with the Pi's internal
pull-up enabled. Override `HANDSET_GPIO_PIN` in `local_prefs.py` when your
wiring needs another BCM pin:

* on-hook/open: GPIO reads high
* off-hook/closed to ground: GPIO reads low

Verify your board numbering before wiring; BCM 11 is not physical header pin
11. A PTT-enabled node fails at startup if `RPi.GPIO` is unavailable. Text/API
and wakeword-only nodes can run without that package.

The systemd service must run as a user that can access GPIO and the selected
audio devices. The native installer uses the account that ran the installer.

## Enable One Device

Configure the handset device in its ignored `local_prefs.py`:

```python
SOURCE_ID = "office_handset"
DEFAULT_ROOM = "office"

HANDSET_PRESENT = True
PTT_ENABLED = True
WAKEWORD_ENABLED = False
HANDSET_GPIO_PIN = 11

ASSISTANT_AUDIO_OUTPUT_MODE = "local"
START_CHIME_DELAY_SECONDS = 0.0
```

`START_CHIME_DELAY_SECONDS` optionally delays the first cue after the handset
goes off-hook so the receiver reaches the user's ear. It does not change audio
capture after the cue.

When `PTT_ENABLED = False`, Home Suite does not poll the handset hook. HTTP,
Telegram, scheduler, physical-button, and wake-word components can continue in
the same process.

## Configure Capture

PTT uses the device's `AUDIO_INPUT_PROFILE`, including stable device matching,
sample rate, hardware mixer enforcement, and `ptt_volume_multiplier`:

```python
AUDIO_INPUT_PROFILE = {
    "name": "office_handset_mic",
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

Stop the service before calibration so the tool owns the microphone:

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

1. Lift the handset; Home Suite resets session state and plays the initial cue.
2. VAD captures one utterance and stops after configured consecutive silence.
3. The transcript enters the shared command/interaction pipeline.
4. While the handset remains up, Home Suite waits for response speech to finish
   and then listens for another utterance.
5. Hang up at any point to cancel capture or immediately terminate local TTS.

Unlike wakeword mode, PTT does not use rolling wakeword endpointing, a cue guard,
or model rearm. It uses `SILENCE_END_MS`, `PRE_ROLL_MS`, `MIN_SPEECH_MS`,
`MAX_UTTERANCE_SECONDS`, and `VAD_MODE` from shared defaults.

The current hosted STT modes require `OPENAI_API_KEY`. `PIPHONE_STT_MODE` may
select streaming or file behavior, but `whisper` still means OpenAI's hosted
Whisper API rather than a local model.

## Interruption and Cancellation

Hanging up is the authoritative PTT interruption gesture. It stops local speech
immediately and prevents an in-flight recording from becoming a command.

While capture is active, exact `cancel`, `never mind`, or `nevermind`
transcripts dismiss the utterance silently and return to the off-hook listening
loop. Longer phrases such as `cancel my timer` continue to their normal command
handler.

Wakeword barge-in is a separate feature and does not modify PTT behavior.

## Operational Checks

```bash
journalctl -u homesuite.service -f -o cat \
  | grep -E 'OFFHOOK_SESSION|PTT_AUDIO_CAPTURE|VAD_|STT_|TRANSCRIPTION_TEXT|ACTION_DECISION'
```

Useful checks:

* `PTT_AUDIO_CAPTURE overflows=0` after repeated utterances
* the configured `AUDIO_INPUT_PROFILE` is selected by stable name
* hanging up during TTS stops playback immediately
* remaining off-hook permits a second command after the response

## Troubleshooting

| Symptom | Check | Likely action |
| --- | --- | --- |
| Lift is not detected | BCM 11 level and pull-up wiring | Confirm BCM numbering and active-low switch behavior. |
| First words are quiet or clipped | calibration output and mixer value | Adjust hardware gain; avoid compensating only in software. |
| Transcripts lose audio | `PTT_AUDIO_CAPTURE overflows` | Use profile `stream_latency="high"`, stop competing capture, and inspect Pi load. |
| It hears the assistant response | output-to-mic placement and cooldown | Reduce acoustic coupling; hanging up remains immediate interruption. |
| No speech after hang-up | expected behavior | Hang-up cancels the current utterance by design. |
