import asyncio
import base64
import json
import os

try:
    from env_compat import install_homesuite_env_aliases
    install_homesuite_env_aliases()
except Exception:
    pass
import queue
import threading
import time
import traceback

import numpy as np

try:
    from scipy.signal import resample_poly as _resample_poly
except Exception:
    _resample_poly = None

# websockets import (module name differs between versions)
try:
    import websockets
except Exception:
    websockets = None

def _get_api_key() -> str:
    k = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if k:
        return k
    try:
        import private_config  # your repo file
        k2 = getattr(private_config, "OPENAI_API_KEY", "") or ""
        k2 = str(k2).strip()
        if k2:
            return k2
    except Exception:
        pass
    return ""


def _pcm16_bytes_to_np(pcm16_bytes: bytes) -> np.ndarray:
    if not pcm16_bytes:
        return np.empty((0,), dtype=np.int16)
    return np.frombuffer(pcm16_bytes, dtype=np.int16)


def _np_to_pcm16_bytes(arr: np.ndarray) -> bytes:
    if arr is None:
        return b""
    if not isinstance(arr, np.ndarray):
        arr = np.asarray(arr)
    if arr.size == 0:
        return b""
    if arr.dtype != np.int16:
        arr = np.clip(np.rint(arr), -32768, 32767).astype(np.int16)
    return arr.tobytes()


def _pcm16_to_mono_bytes(raw: bytes, channels: int) -> bytes:
    if not raw:
        return b""
    ch = int(channels or 1)
    if ch <= 1:
        return raw

    arr = np.frombuffer(raw, dtype=np.int16)
    if arr.size == 0:
        return b""

    usable = (arr.size // ch) * ch
    if usable <= 0:
        return b""

    arr = arr[:usable].reshape(-1, ch)

    if ch == 2:
        mono = ((arr[:, 0].astype(np.int32) + arr[:, 1].astype(np.int32)) // 2).astype(np.int16)
    else:
        mono = np.rint(arr.astype(np.float32).mean(axis=1)).astype(np.int16)

    return mono.tobytes()


def _pcm16_resample_bytes(raw: bytes, sr_in: int, sr_out: int) -> bytes:
    if not raw:
        return b""

    try:
        sr_in = int(sr_in)
        sr_out = int(sr_out)
    except Exception:
        return raw

    if sr_in <= 0 or sr_out <= 0 or sr_in == sr_out:
        return raw

    arr = np.frombuffer(raw, dtype=np.int16)
    if arr.size == 0:
        return b""

    if _resample_poly is not None:
        try:
            import math
            g = math.gcd(sr_in, sr_out)
            up = sr_out // g
            down = sr_in // g
            out = _resample_poly(arr.astype(np.float32, copy=False), up=up, down=down)
            return _np_to_pcm16_bytes(out)
        except Exception:
            pass

    # Fallback: simple nearest-neighbor index remap.
    # Not as high quality as polyphase resampling, but safe and dependency-light.
    try:
        n_out = max(1, int(round(len(arr) * float(sr_out) / float(sr_in))))
        idx = np.linspace(0, len(arr) - 1, num=n_out)
        out = arr[np.clip(np.rint(idx).astype(np.int64), 0, len(arr) - 1)]
        return _np_to_pcm16_bytes(out)
    except Exception:
        return raw


def _ws_connect(url: str, headers: dict):
    """
    websockets client API changed over time.
    We'll try the common kwargs in order.
    """
    if websockets is None:
        raise RuntimeError("websockets is not installed/importable")

    # Newer websockets: additional_headers=
    try:
        return websockets.connect(url, additional_headers=headers)
    except TypeError:
        pass
    # Older websockets: extra_headers=
    try:
        return websockets.connect(url, extra_headers=headers)
    except TypeError:
        pass
    # Some variants accept headers=
    return websockets.connect(url, headers=headers)

class StreamingTranscriber:
    """
    Background-thread async websocket client.
    Main thread feeds raw int16 PCM frames (bytes) at sr_in (typically 48000).
    We downsample in the sender to sr_out=24000 and append to input_audio_buffer.
    On commit, we wait for conversation.item.input_audio_transcription.completed
    and return the transcript.
    """

    def __init__(
        self,
        model: str = "gpt-4o-transcribe",
        language: str = "en",
        sr_out: int = 24000,
        turn_detection: dict = None,
        manual_commit: bool = False,
        debug: bool = False,
        timeout_s: float = 4.0,
    ):
        self.model = model
        self.language = language
        self.sr_out = int(sr_out)
        self.turn_detection = turn_detection
        self.manual_commit = bool(manual_commit)
        self.debug = bool(debug)
        self.timeout_s = float(timeout_s)

        self._q = queue.Queue()
        self._done_evt = threading.Event()
        self._ready_evt = threading.Event()
        self._stop_evt = threading.Event()
        self._err = None
        self._transcript = ""

        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()

        # wait until websocket is connected & session updated (or failed)
        if not self._ready_evt.wait(timeout=8.0):
            raise RuntimeError("StreamingTranscriber did not become ready (timeout)")
        if self._err:
            raise RuntimeError(f"StreamingTranscriber init failed: {self._err}")

    def append_pcm16(self, pcm16_bytes: bytes, sr_in: int):
        """
        Feed one chunk of mono int16 PCM at sr_in.
        """
        if self._stop_evt.is_set():
            return
        if not pcm16_bytes:
            return
        self._q.put(("append", (pcm16_bytes, int(sr_in))))

    def commit_and_wait(self) -> str:
        """
        Commit the buffer and wait for transcript.
        """
        if self._stop_evt.is_set():
            return ""
        self._q.put(("commit", None))
        ok = self._done_evt.wait(timeout=self.timeout_s)
        if not ok:
            self._stop_evt.set()
            raise TimeoutError("Timed out waiting for transcription completion")
        if self._err:
            raise RuntimeError(self._err)
        return (self._transcript or "").strip()

    def cancel(self):
        """
        Best-effort cancel: stop thread loop.
        """
        self._stop_evt.set()
        try:
            self._q.put(("stop", None))
        except Exception:
            pass

    # ---------------- internal ----------------

    def _thread_main(self):
        try:
            asyncio.run(self._async_main())
        except Exception as e:
            self._err = f"thread_main error: {e!r}"
            if self.debug:
                traceback.print_exc()
            self._ready_evt.set()
            self._done_evt.set()

    async def _async_main(self):
        api_key = _get_api_key()
        if not api_key:
            self._err = "OPENAI_API_KEY is not set (env or private_config.py)"
            self._ready_evt.set()
            self._done_evt.set()
            return
        if websockets is None:
            self._err = "websockets is not installed"
            self._ready_evt.set()
            self._done_evt.set()
            return

        # Realtime transcription session endpoint.
        # GA migration (May 12, 2026): the beta API shape was disabled.
        # Key changes from beta:
        #   - Drop "OpenAI-Beta: realtime=v1" header
        #   - session.created/updated (was transcription_session.created/updated)
        #   - session.update body restructured: audio.input.{format,transcription}
        #     with format as {type: "audio/pcm", rate: 24000} object
        #   - session.type = "transcription" required
        #   - Server VAD is on by default and auto-commits on end-of-speech
        url = f"wss://api.openai.com/v1/realtime?intent=transcription"
        headers = {
            "Authorization": f"Bearer {api_key}",
        }

        async with _ws_connect(url, headers) as ws:
            # Wait for session.created
            await self._recv_until(ws, want_types={"session.created"})

            # GA session.update: nested audio.input.{format,transcription}.
            input_config = {
                "format": {"type": "audio/pcm", "rate": int(self.sr_out)},
                "transcription": {
                    "model": self.model,
                    "language": self.language,
                },
            }
            if self.manual_commit:
                input_config["turn_detection"] = None
            elif self.turn_detection is not None:
                input_config["turn_detection"] = self.turn_detection

            upd = {
                "type": "session.update",
                "session": {
                    "type": "transcription",
                    "audio": {
                        "input": input_config,
                    },
                },
            }

            await ws.send(json.dumps(upd))

            # Wait for updated (or error)
            got = await self._recv_until(ws, want_types={"session.updated", "error"})
            if got.get("type") == "error":
                self._err = f"server error during session.update: {got}"
                self._ready_evt.set()
                self._done_evt.set()
                return

            # Ready to accept audio
            self._ready_evt.set()

            state = None  # kept for compatibility; resampling is now stateless per chunk

            while not self._stop_evt.is_set():
                # drain queue with small timeout so we can also receive server events
                try:
                    kind, payload = self._q.get(timeout=0.02)
                except queue.Empty:
                    kind = None

                # pump server messages (non-blocking-ish)
                await self._drain_incoming(ws)

                if kind is None:
                    continue

                if kind == "append":
                    pcm16_bytes, sr_in = payload
                    # Default: trust caller (who may already resample to sr_out=24000).
                    # Optional fallback: enable resample if sr_in != sr_out via env flag.
                    allow_fallback_resample = (os.getenv("PIPHONE_RT_ALLOW_FALLBACK_RESAMPLE", "0").strip() == "1")
                    try:
                        if sr_in != self.sr_out:
                            if allow_fallback_resample:
                                pcm16_bytes = _pcm16_resample_bytes(
                                    pcm16_bytes,
                                    sr_in=sr_in,
                                    sr_out=self.sr_out,
                                )
                            else:
                                if self.debug:
                                    print(f"[streaming_stt] WARNING: sr_in={sr_in} != sr_out={self.sr_out}; sending raw")
                    except Exception as e:
                        # If fallback resample fails, send raw chunk as-is.
                        if self.debug:
                            print(f"[streaming_stt] resample failed: {e!r}")
                    b64 = base64.b64encode(pcm16_bytes).decode("ascii")
                    msg = {"type": "input_audio_buffer.append", "audio": b64}
                    await ws.send(json.dumps(msg))

                elif kind == "commit":
                    # GA: server VAD typically auto-commits on end-of-speech. We
                    # still send an explicit commit as a safety for audio that
                    # ends without a clear silence; if the server already
                    # committed, it returns input_audio_buffer_commit_empty
                    # which we tolerate and keep waiting for the transcript.
                    try:
                        await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                    except Exception:
                        pass

                    # Wait for transcription completion
                    transcript = ""
                    t0 = time.time()
                    while (time.time() - t0) < self.timeout_s and not self._stop_evt.is_set():
                        ev = await self._recv_one(ws)
                        if not ev:
                            continue
                        ev_type = ev.get("type")
                        if ev_type == "error":
                            err = ev.get("error") or {}
                            err_code = err.get("code") if isinstance(err, dict) else None
                            # Server VAD already committed — keep listening for transcript
                            if err_code == "input_audio_buffer_commit_empty":
                                continue
                            self._err = f"server error: {ev}"
                            self._done_evt.set()
                            return
                        if ev_type == "conversation.item.input_audio_transcription.completed":
                            transcript = (ev.get("transcript") or "").strip()
                            break
                        # some servers may wrap it; ignore others

                    self._transcript = transcript
                    self._done_evt.set()
                    return

                elif kind == "stop":
                    return

    async def _recv_one(self, ws):
        try:
            raw = await ws.recv()
        except Exception:
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return {"type": "raw", "raw": raw}

    async def _recv_until(self, ws, want_types: set, timeout_s: float = 8.0):
        t0 = time.time()
        while (time.time() - t0) < timeout_s and not self._stop_evt.is_set():
            ev = await self._recv_one(ws)
            if not ev:
                continue
            if self.debug:
                print(f"[server] {ev}")
            if ev.get("type") in want_types:
                return ev
            if ev.get("type") == "error":
                return ev
        return {"type": "timeout"}

    async def _drain_incoming(self, ws):
        # best-effort: do not block; just return if nothing
        try:
            while True:
                # websockets doesn't expose a clean "try_recv"; use timeout 0
                ev = await asyncio.wait_for(self._recv_one(ws), timeout=0.0)
                if not ev:
                    return
                if self.debug:
                    print(f"[server] {ev}")
                if ev.get("type") == "error":
                    err = ev.get("error") or {}
                    err_code = err.get("code") if isinstance(err, dict) else None
                    # Harmless: server VAD already auto-committed
                    if err_code == "input_audio_buffer_commit_empty":
                        continue
                    self._err = f"server error: {ev}"
                    self._done_evt.set()
                    self._stop_evt.set()
                    return
        except asyncio.TimeoutError:
            return
        except Exception:
            return


# --------------------------------------------------------------------
# Canonical WAV-file transcription entrypoint used by main.py and
# realtime_transcribe.py compatibility paths.
# --------------------------------------------------------------------
def transcribe_wav_file(wav_path: str) -> str:
    """
    Transcribe a WAV file via StreamingTranscriber in this module.
    Returns transcript text (may be empty string).
    """
    import os
    import wave
    import inspect

    model = (os.getenv("PIPHONE_RT_MODEL") or "gpt-4o-transcribe").strip()
    language = (os.getenv("PIPHONE_RT_LANGUAGE") or os.getenv("PIPHONE_RT_LANG") or "en").strip()

    # Load WAV -> raw bytes (expect 16-bit PCM)
    with wave.open(wav_path, "rb") as wf:
        nch = wf.getnchannels()
        sw = wf.getsampwidth()
        sr = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)

    if sw != 2:
        raise RuntimeError(f"Expected 16-bit PCM WAV (sampwidth=2). Got sampwidth={sw} for {wav_path}")

    # Stereo -> mono
    if nch == 2:
        raw = _pcm16_to_mono_bytes(raw, channels=2)
        nch = 1
    elif nch != 1:
        raise RuntimeError(f"Expected mono/stereo WAV. Got channels={nch} for {wav_path}")

    # GA Realtime PCM input is 24 kHz. Keep the completed WAV in one manually
    # committed turn so a wakeword prefix cannot auto-commit ahead of the
    # actual command.
    target_sr = 24000
    if sr != target_sr:
        raw = _pcm16_resample_bytes(raw, sr_in=sr, sr_out=target_sr)
        sr = target_sr

    # 20ms chunks @ 24kHz => 480 samples => 960 bytes
    chunk_bytes = 960

    st = None
    try:
        # Construct transcriber (be tolerant to differing __init__ signatures)
        try:
            st = StreamingTranscriber(
                model=model,
                language=language,
                sr_out=target_sr,
                manual_commit=True,
                timeout_s=8.0,
            )
        except TypeError:
            try:
                st = StreamingTranscriber(model, language=language, sr_out=target_sr)
            except TypeError:
                st = StreamingTranscriber(model)

        # Determine whether append_pcm16 expects sr_in
        needs_sr_in = False
        try:
            sig = inspect.signature(st.append_pcm16)
            # bound method signature won't include self
            needs_sr_in = ("sr_in" in sig.parameters) or (len(sig.parameters) >= 2)
        except Exception:
            # If signature introspection fails, assume sr_in is needed based on your error
            needs_sr_in = True

        for i in range(0, len(raw), chunk_bytes):
            chunk = raw[i:i + chunk_bytes]
            if not chunk:
                continue
            if needs_sr_in:
                # Prefer keyword if available
                try:
                    st.append_pcm16(chunk, sr_in=sr)
                except TypeError:
                    st.append_pcm16(chunk, sr)
            else:
                st.append_pcm16(chunk)

        out = st.commit_and_wait()
        if out is None:
            return ""

        if isinstance(out, dict):
            for k in ("text", "transcript", "transcription"):
                v = out.get(k)
                if isinstance(v, str):
                    return v.strip()
            return str(out).strip()

        return str(out).strip()

    finally:
        try:
            if st is not None:
                st.cancel()
        except Exception:
            pass
