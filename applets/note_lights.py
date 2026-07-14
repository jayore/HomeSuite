#!/usr/bin/env python3
"""
note_lights.py — Instrument note → Home Assistant light applet

Maps notes played on an instrument to HA light actions in real time.
Two modes (set MODE below):
  "notes"     — each note fires its mapped action immediately
  "sequences" — a specific sequence of notes fires an action

USAGE:
    # Stop PTT service first to free the mic:
    sudo systemctl stop homesuite.service

    # Run the applet:
    ~/homesuite/.venv/bin/python ~/homesuite/applets/note_lights.py

STOP:
    Ctrl+C  (or play the EXIT_NOTE if configured)

HOMESUITE INTEGRATION:
    The applet lifecycle is managed by applet_controls.py, including spoken
    start, stop, toggle, and status commands.

OPTIONAL DEPENDENCIES:
    Install applets/requirements-note-lights.txt on the device that will run
    this applet. The core Home Suite installer does not install this heavier
    pitch-analysis stack.
"""

from __future__ import annotations

import sys
import os
import time
from pathlib import Path
import signal
import logging
from collections import deque
from typing import Callable, Optional

import numpy as np
import sounddevice as sd
import librosa

# ── path: allow importing ha_client from the HomeSuite project root ───────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
import ha_client as ha
from private_config import HA_URL, HA_TOKEN

# ───────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("note_lights")


# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURE HERE
# ═══════════════════════════════════════════════════════════════════════════

# ── Audio ──────────────────────────────────────────────────────────────────
SAMPLE_RATE   = int(os.environ.get("PIPHONE_SD_SAMPLERATE", "48000") or "48000")
CHUNK_SAMPLES = 4096    # ~85 ms at 48 kHz; increase if too jumpy
AUDIO_DEVICE  = None    # None = system default; set to device index or name
                        # to target a specific mic (use --list-devices to find)

# ── Pitch detection ────────────────────────────────────────────────────────
# PITCH_FMIN is critical: when YIN can't find a clear pitch (silence, noise,
# note decay tail), it tends to report the bottom of its search range. Set
# this just below the lowest note your instrument can play.
#
#   Standard ukulele (GCEA, re-entrant high-G):  lowest = C4   = 261.6 Hz
#   Low-G ukulele:                                lowest = G3   = 196.0 Hz
#   Guitar (standard):                            lowest = E2   =  82.4 Hz
#
# Default 250 Hz works for standard ukulele (just below C4) and also rejects
# direct detection of human speech fundamentals (male ~85-180 Hz, female
# ~165-255 Hz). If you play low-G ukulele or guitar, lower this — but doing
# so will let speech through more easily.
PITCH_FMIN     = 250.0
PITCH_FMAX     = 2000.0 # Hz — highest note to detect

AMPLITUDE_MIN  = 0.015  # RMS below this = silence, skip (0.0–1.0 float range)

# CONFIDENCE_MIN filters by pitch stability. Plucked strings have very stable
# pitch across the detection window; speech wobbles. Raising this is the
# main lever for rejecting talking and other non-instrument sound.
#   0.5 — permissive, may fire on sustained vowels
#   0.6 — balanced (recommended)
#   0.7+ — strict, may miss soft / fast-decay plucks
CONFIDENCE_MIN = 0.6

STABILITY_HITS = 2      # consecutive chunks on the same note before firing
                        # (2 = ~186ms of stable pitch; raise if too trigger-happy)

# ── Light transition ───────────────────────────────────────────────────────
# Seconds for the bulb to fade from one color to the next.
#   0.0   — instant snap (most responsive feel)
#   0.2   — quick crossfade
#   0.5+  — slow, dreamy crossfade
# Without this we use whatever the bulb's default transition is, which is
# usually ~1 s on smart bulbs and feels sluggish.
TRANSITION_SEC = 0.0

# ── Debounce ───────────────────────────────────────────────────────────────
SAME_NOTE_COOLDOWN_SEC = 1.5  # min seconds before same note fires again

# ── Exit note ──────────────────────────────────────────────────────────────
# Play this note to exit the applet without Ctrl+C.
# Useful once PiPhone start/stop integration is in place.
# Set to None to disable.
EXIT_NOTE = None   # e.g. "C2" — a note below normal playing range


# ── Entities ───────────────────────────────────────────────────────────────
SIDE_LAMP = "light.side_lamp"   # ← change to your actual HA entity_id


# ── Action helpers ─────────────────────────────────────────────────────────
# These return callables (taking a single `note` arg) that you bind to notes
# or sequences in the maps below. Most helpers ignore the note arg —
# continuous_hue() is the one that actually uses it.

def light_color(entity, r, g, b, brightness=200):
    """Set a light to a fixed RGB color (ignores the note)."""
    def _do(note=None):
        ok = ha.call_ha_service("light/turn_on", {
            "entity_id": entity,
            "rgb_color": [r, g, b],
            "brightness": brightness,
            "transition": TRANSITION_SEC,
        })
        log.info("HA  light_color  entity=%s  rgb=[%d,%d,%d]  ok=%s", entity, r, g, b, ok)
    return _do

def light_off(entity):
    """Turn a light off."""
    def _do(note=None):
        ok = ha.call_ha_service("light/turn_off", {
            "entity_id": entity,
            "transition": TRANSITION_SEC,
        })
        log.info("HA  light_off  entity=%s  ok=%s", entity, ok)
    return _do

def light_scene(scene_entity):
    """Activate a scene."""
    def _do(note=None):
        ok = ha.call_ha_service("scene/turn_on", {"entity_id": scene_entity})
        log.info("HA  scene  entity=%s  ok=%s", scene_entity, ok)
    return _do

# ── Continuous-hue mapping ─────────────────────────────────────────────────
# Maps each of the 12 chromatic notes to a hue 30° apart on the color wheel.
# Octave is ignored — all C's get the same hue, all A's the same, etc.
#
#   C   0°   red          G   210°  azure
#   C# 30°   orange       G#  240°  blue
#   D  60°   yellow       A   270°  violet
#   D# 90°   chartreuse   A#  300°  magenta
#   E  120°  green        B   330°  pink
#   F  150°  spring green
#   F# 180°  cyan
NOTE_TO_HUE = {
    "C":   0, "C#":  30, "D":  60, "D#":  90,
    "E": 120, "F":  150, "F#":180, "G":  210,
    "G#":240, "A":  270, "A#":300, "B":  330,
}

def continuous_hue(entity, brightness=200, saturation=100):
    """
    Set a light's hue based on the note played.
    `saturation` is HA's 0–100 percent; `brightness` is 0–255.
    """
    def _do(note: str):
        note_class = _strip_octave(note)
        hue = NOTE_TO_HUE.get(note_class)
        if hue is None:
            log.debug("continuous_hue  unknown note class=%r", note_class)
            return
        ok = ha.call_ha_service("light/turn_on", {
            "entity_id": entity,
            "hs_color": [hue, saturation],
            "brightness": brightness,
            "transition": TRANSITION_SEC,
        })
        log.info("HA  continuous_hue  entity=%s  note=%s  hue=%d°  ok=%s",
                 entity, note, hue, ok)
    return _do


# ── Default note action ───────────────────────────────────────────────────
# Runs for any detected note that isn't in NOTE_ACTIONS below.
# Set to None to make unmapped notes just log without doing anything.
DEFAULT_NOTE_ACTION = continuous_hue(SIDE_LAMP, brightness=200, saturation=100)


# ── Note → Action overrides ────────────────────────────────────────────────
# Optional. If a note matches a key here, this fires INSTEAD of the default.
# Key forms:
#   "A4"  — matches only A in octave 4 (specific)
#   "A"   — matches any A regardless of octave (octave-agnostic)
# If both forms are present, the specific (octave-bearing) key wins.
# Sharp notation only — use "C#" or "C#4", never "Db".
#
# Examples (uncomment to use):
#   "C5":  light_off(SIDE_LAMP),                       # high C → lights off
#   "A":   light_color(SIDE_LAMP, 255, 255, 255),      # any A → pure white
#   "G4":  light_scene("scene.party"),                 # G4 → activate scene
NOTE_ACTIONS = {}


# ── Sequence → Action map ──────────────────────────────────────────────────
# Sequences ALWAYS run alongside individual notes. Each detected note fires
# its individual action (NOTE_ACTIONS / DEFAULT_NOTE_ACTION) AND also gets
# pushed into the sequence matcher. If the recent note history matches a
# sequence below, that action fires too (overriding the individual note's
# color since it comes last).
# Keys are tuples of note names in order.
# The last N notes played are compared against each sequence.
# If the gap between any two consecutive notes exceeds SEQUENCE_TIMEOUT_SEC,
# the note buffer resets.
SEQUENCE_TIMEOUT_SEC = 3.0

SEQUENCE_ACTIONS = {
    ("C4", "E4", "G4"): light_color(SIDE_LAMP, 255, 255, 255),  # C triad → white
    ("A4", "A4", "A4"): light_off(SIDE_LAMP),                   # triple-A → off
    ("G4", "A4", "B4"): light_scene("scene.movie_time"),        # ascending run → scene
}

# ═══════════════════════════════════════════════════════════════════════════
# END CONFIG
# ═══════════════════════════════════════════════════════════════════════════


# ───────────────────────────────────────────────────────────────────────────
# Pitch detection
# ───────────────────────────────────────────────────────────────────────────

class PitchDetector:
    """
    Detects the dominant pitch in an audio chunk using librosa.yin().

    yin() returns a per-hop array of frequency estimates. Taking the median
    across hops gives one robust frequency per chunk. Chunks where the note
    frequency is highly variable (chord, noise, silence) produce low
    confidence and are suppressed upstream.
    """

    def detect(self, samples: np.ndarray) -> tuple[str | None, float]:
        """
        Args:
            samples: float32 mono array in [-1.0, 1.0], length >= 512

        Returns:
            (note_name, confidence)  where note_name is like "A4" or None,
            and confidence is 0.0–1.0 (rough measure of pitch stability).
        """
        if len(samples) < 512:
            return None, 0.0

        # YIN pitch estimator — returns array of Hz values, one per hop
        freqs = librosa.yin(
            samples,
            fmin=PITCH_FMIN,
            fmax=PITCH_FMAX,
            sr=SAMPLE_RATE,
        )

        valid = freqs[(freqs >= PITCH_FMIN) & (freqs <= PITCH_FMAX)]
        if len(valid) == 0:
            return None, 0.0

        median_freq = float(np.median(valid))

        # Confidence: low frequency variance = stable pitch = high confidence.
        # A ukulele note should stay close to one frequency throughout the chunk.
        if len(valid) >= 3:
            cv = float(np.std(valid) / np.mean(valid))   # coefficient of variation
            confidence = max(0.0, 1.0 - cv * 3.0)
        else:
            confidence = 0.3

        try:
            # unicode=False → ASCII sharps ("C#4") rather than Unicode ("C♯4").
            # Critical: without this, user-written keys like "C#4" silently
            # never match librosa's default Unicode output.
            note_name = librosa.hz_to_note(median_freq, unicode=False)
        except Exception:
            return None, 0.0

        return note_name, confidence


# ───────────────────────────────────────────────────────────────────────────
# Note lookup — supports octave-specific and octave-agnostic keys
# ───────────────────────────────────────────────────────────────────────────

def _strip_octave(note: str) -> str:
    """'C#4' -> 'C#', 'A4' -> 'A', 'A' -> 'A'."""
    return note.rstrip("0123456789")

def _lookup_note_action(note: str):
    """
    Look up the action for a detected note.
    Precedence: exact octave match (e.g. 'A4') beats note-class match ('A').
    """
    if note in NOTE_ACTIONS:
        return NOTE_ACTIONS[note]
    return NOTE_ACTIONS.get(_strip_octave(note))


# ───────────────────────────────────────────────────────────────────────────
# Debounce — require stability before firing
# ───────────────────────────────────────────────────────────────────────────

class NoteDebouncer:
    """
    Converts a raw stream of (note, confidence) detections into discrete
    note-fired events.

    Rules:
      - A note must appear in STABILITY_HITS consecutive chunks to fire.
      - The same note won't fire again until SAME_NOTE_COOLDOWN_SEC has passed.
      - A different note resets the stability counter immediately.
    """

    def __init__(self):
        self._candidate: str | None = None
        self._streak: int = 0
        self._last_fired: dict[str, float] = {}   # note → timestamp

    def push(self, note: str | None) -> str | None:
        """
        Feed the latest detected note name (or None for silence/noise).
        Returns the note name if it should fire now, else None.
        """
        if note != self._candidate:
            self._candidate = note
            self._streak = 0

        if note is None:
            return None

        self._streak += 1

        if self._streak < STABILITY_HITS:
            return None

        # Stable. Check cooldown.
        now = time.monotonic()
        last = self._last_fired.get(note, 0.0)
        if (now - last) < SAME_NOTE_COOLDOWN_SEC:
            return None

        self._last_fired[note] = now
        self._streak = 0   # reset so it won't re-fire immediately on next chunk
        return note


# ───────────────────────────────────────────────────────────────────────────
# Sequence matching
# ───────────────────────────────────────────────────────────────────────────

class SequenceMatcher:
    """
    Maintains a rolling buffer of recent note events and matches them against
    registered sequences.

    A sequence matches when the last N notes in the buffer exactly equal the
    registered sequence, AND no gap between consecutive notes exceeded
    SEQUENCE_TIMEOUT_SEC.
    """

    def __init__(self, actions: dict):
        self._actions = actions   # (note_tuple) → callable
        max_len = max((len(k) for k in actions), default=0)
        self._buffer: deque = deque(maxlen=max(max_len, 1))

    def buffer_notes(self) -> list:
        """Return current buffered note names (no timestamps) — for logging."""
        return [n for n, _ in self._buffer]

    def push(self, note: str) -> Optional[Callable]:
        """
        Add a note to the sequence buffer. Returns a matching action callable
        or None.
        """
        now = time.monotonic()

        # Check gap from previous note — reset if too long
        if self._buffer:
            _, prev_ts = self._buffer[-1]
            if (now - prev_ts) > SEQUENCE_TIMEOUT_SEC:
                log.debug("SEQ  timeout — buffer cleared")
                self._buffer.clear()

        self._buffer.append((note, now))

        # Check all registered sequences against the tail of the buffer
        notes_only = [n for n, _ in self._buffer]
        for seq_tuple, action in self._actions.items():
            seq = list(seq_tuple)
            if notes_only[-len(seq):] == seq:
                log.info("SEQ  match  sequence=%s", seq_tuple)
                self._buffer.clear()
                return action

        return None


# ───────────────────────────────────────────────────────────────────────────
# Main app
# ───────────────────────────────────────────────────────────────────────────

class NoteLightsApp:

    def __init__(self):
        self._detector  = PitchDetector()
        self._debouncer = NoteDebouncer()
        self._seq       = SequenceMatcher(SEQUENCE_ACTIONS)
        self._running   = False

    # ── dispatch ────────────────────────────────────────────────────────────

    def _dispatch(self, note: str):
        """Fire individual note action AND feed the sequence matcher."""
        if note == EXIT_NOTE:
            log.info("EXIT_NOTE received — stopping")
            self._running = False
            return

        # 1) Individual note action: explicit NOTE_ACTIONS first, then DEFAULT.
        action = _lookup_note_action(note)
        source = "override"
        if action is None and DEFAULT_NOTE_ACTION is not None:
            action = DEFAULT_NOTE_ACTION
            source = "default"

        if action is not None:
            log.info("NOTE  %s  → %s", note, source)
            try:
                action(note)
            except Exception:
                log.exception("action failed for note=%s", note)
        else:
            log.info("NOTE  %s  (no action mapped)", note)

        # 2) Sequence matcher — runs in parallel. If it matches, the sequence
        # action fires AFTER the individual one (so it visually wins).
        seq_action = self._seq.push(note)
        if seq_action is not None:
            try:
                seq_action(note)
            except Exception:
                log.exception("action failed for sequence ending in note=%s", note)
        else:
            log.debug("SEQ  push  note=%s  buffer=%s",
                      note, self._seq.buffer_notes())

    # ── main loop ───────────────────────────────────────────────────────────

    def run(self):
        ha.configure_ha(ha_url=HA_URL, ha_token=HA_TOKEN)
        log.info("HA configured  url=%s", HA_URL)
        log.info("Device: %s", AUDIO_DEVICE or "default")
        log.info("Sample rate: %s Hz", SAMPLE_RATE)

        # Warm up librosa — numba JIT-compiles on first call (~1–2 s on Pi).
        # Without this, the first real note after launch is missed.
        log.info("Warming up pitch detector…")
        _warmup = np.sin(2 * np.pi * 440.0 *
                         np.linspace(0, CHUNK_SAMPLES / SAMPLE_RATE,
                                     CHUNK_SAMPLES, dtype=np.float32)) * 0.3
        self._detector.detect(_warmup)
        log.info("Ready.")

        log.info("Default action: %s",
                 "continuous_hue" if DEFAULT_NOTE_ACTION else "(none)")
        log.info("Note overrides: %s", sorted(NOTE_ACTIONS.keys()) or "(none)")
        log.info("Sequences:      %s",
                 [list(k) for k in SEQUENCE_ACTIONS] or "(none)")
        log.info("Listening… (Ctrl+C to stop)")

        self._running = True

        # Status heartbeat — every STATUS_INTERVAL_SEC, print what we've
        # been hearing so the user can diagnose silence / low confidence /
        # wrong-octave issues without having to enable debug logging.
        STATUS_INTERVAL_SEC = 4.0
        last_status = time.monotonic()
        peak_rms = 0.0
        chunks_above_amp = 0
        chunks_total = 0
        notes_seen = {}   # note_name → count of times detected (high-confidence)

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=CHUNK_SAMPLES,
                device=AUDIO_DEVICE,
            ) as stream:

                while self._running:
                    chunk, overflowed = stream.read(CHUNK_SAMPLES)
                    if overflowed:
                        log.debug("audio buffer overflow")

                    samples = chunk[:, 0]   # (N,1) → (N,)
                    chunks_total += 1

                    rms = float(np.sqrt(np.mean(samples ** 2)))
                    peak_rms = max(peak_rms, rms)

                    # Periodic status heartbeat
                    now = time.monotonic()
                    if (now - last_status) >= STATUS_INTERVAL_SEC:
                        if notes_seen:
                            top = sorted(notes_seen.items(), key=lambda x: -x[1])[:5]
                            seen_str = ", ".join(f"{n}×{c}" for n, c in top)
                        else:
                            seen_str = "—"
                        log.info(
                            "STATUS  peak_rms=%.4f (gate=%.4f)  audible_chunks=%d/%d  notes_heard=[%s]",
                            peak_rms, AMPLITUDE_MIN, chunks_above_amp, chunks_total, seen_str,
                        )
                        last_status = now
                        peak_rms = 0.0
                        chunks_above_amp = 0
                        chunks_total = 0
                        notes_seen = {}

                    # Amplitude gate — skip silence
                    if rms < AMPLITUDE_MIN:
                        self._debouncer.push(None)
                        continue
                    chunks_above_amp += 1

                    # Pitch detect
                    note, confidence = self._detector.detect(samples)
                    log.debug("raw  note=%-5s  conf=%.2f  rms=%.4f", note, confidence, rms)

                    # Stability gate — ignore low-confidence detections
                    if confidence < CONFIDENCE_MIN or note is None:
                        self._debouncer.push(None)
                        continue

                    # Tally for status heartbeat
                    notes_seen[note] = notes_seen.get(note, 0) + 1

                    # Debounce → fire
                    fired = self._debouncer.push(note)
                    if fired:
                        self._dispatch(fired)

        except KeyboardInterrupt:
            log.info("Stopped by user")
        except Exception:
            self._exit_code = 1
            log.exception("Fatal error in audio loop")
        finally:
            log.info("note_lights exiting")


# ───────────────────────────────────────────────────────────────────────────
# Entry point
# ───────────────────────────────────────────────────────────────────────────

def _list_devices():
    print("\nAvailable audio input devices:")
    try:
        default_in = sd.default.device[0]
    except Exception:
        default_in = None
    for i, dev in enumerate(sd.query_devices()):
        if dev.get("max_input_channels", 0) > 0:
            marker = "  ← default" if i == default_in else ""
            print(f"  [{i:2d}]  {dev['name']:40s}  "
                  f"ch={dev['max_input_channels']}  "
                  f"sr={int(dev.get('default_samplerate', 0))}{marker}")
    print()


def _meter_mode(device):
    """Just show input level — no pitch detection. For verifying the mic works."""
    print(f"\nLevel meter — device={device or 'default'}  (Ctrl+C to stop)\n")
    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                            blocksize=CHUNK_SAMPLES, device=device) as stream:
            while True:
                chunk, _ = stream.read(CHUNK_SAMPLES)
                rms = float(np.sqrt(np.mean(chunk[:, 0] ** 2)))
                bars = int(min(rms * 200, 60))
                print(f"  rms={rms:.4f}  {'█' * bars}")
    except KeyboardInterrupt:
        print("\nstopped.")


def _parse_device_arg(argv):
    """Returns the device (int index, str substring, or None) from --device <X>."""
    if "--device" not in argv:
        return None
    i = argv.index("--device")
    if i + 1 >= len(argv):
        print("error: --device requires a value (index or name substring)")
        sys.exit(2)
    val = argv[i + 1]
    try:
        return int(val)
    except ValueError:
        return val  # sounddevice accepts a name substring


def _parse_sample_rate_arg(argv):
    if "--samplerate" not in argv:
        return None
    i = argv.index("--samplerate")
    if i + 1 >= len(argv):
        print("error: --samplerate requires a value, e.g. 48000")
        sys.exit(2)
    try:
        sr = int(argv[i + 1])
    except ValueError:
        print("error: --samplerate must be an integer, e.g. 48000")
        sys.exit(2)
    if sr <= 0:
        print("error: --samplerate must be positive")
        sys.exit(2)
    return sr


if __name__ == "__main__":
    if "--list-devices" in sys.argv:
        _list_devices()
        sys.exit(0)

    if "-v" in sys.argv or "--verbose" in sys.argv:
        logging.getLogger().setLevel(logging.DEBUG)
        log.debug("verbose logging enabled")

    sample_rate_arg = _parse_sample_rate_arg(sys.argv)
    if sample_rate_arg is not None:
        SAMPLE_RATE = sample_rate_arg
        log.info("Sample-rate override from CLI: %r", SAMPLE_RATE)

    device_arg = _parse_device_arg(sys.argv)
    if device_arg is not None:
        AUDIO_DEVICE = device_arg
        log.info("Audio device override from CLI: %r", AUDIO_DEVICE)

    if "--meter" in sys.argv:
        _meter_mode(AUDIO_DEVICE)
        sys.exit(0)

    app = NoteLightsApp()
    signal.signal(signal.SIGTERM, lambda *_: setattr(app, "_running", False))
    app.run()
    raise SystemExit(int(getattr(app, "_exit_code", 0) or 0))
