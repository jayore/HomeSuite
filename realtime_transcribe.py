"""
Compat shim for versions of main.py that import:
    from realtime_transcribe import realtime_transcribe_wav

Routes to realtime_streaming_stt.py helpers if present.
"""

from __future__ import annotations
from typing import Optional

def realtime_transcribe_wav(
    wav_path: str,
    model: Optional[str] = None,
    language: Optional[str] = "en",
    timeout_s: float = 30.0,
    **kwargs,
) -> str:
    import realtime_streaming_stt as rss

    # Most common in your codebase history
    if hasattr(rss, "transcribe_wav_file"):
        try:
            return (rss.transcribe_wav_file(wav_path, model=model, language=language, timeout_s=timeout_s) or "").strip()
        except TypeError:
            return (rss.transcribe_wav_file(wav_path) or "").strip()

    if hasattr(rss, "realtime_transcribe_wav"):
        return (rss.realtime_transcribe_wav(wav_path, model=model, language=language, timeout_s=timeout_s, **kwargs) or "").strip()

    if hasattr(rss, "StreamingTranscriber"):
        st = rss.StreamingTranscriber(model=model) if model else rss.StreamingTranscriber()
        if hasattr(st, "transcribe_wav_file"):
            return (st.transcribe_wav_file(wav_path) or "").strip()

    raise ImportError(
        "No suitable transcribe function found in realtime_streaming_stt.py "
        "(expected transcribe_wav_file / realtime_transcribe_wav / StreamingTranscriber)."
    )
