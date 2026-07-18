#!/usr/bin/env python3
"""Validate one OpenWakeWord ONNX classifier without loading it in the console."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def emit(payload: dict) -> None:
    print(json.dumps(payload, separators=(",", ":")))


def main() -> int:
    if len(sys.argv) != 2:
        emit({"ok": False, "error": "A model path is required."})
        return 2
    path = Path(sys.argv[1]).resolve()
    if path.suffix.lower() != ".onnx" or not path.is_file():
        emit({"ok": False, "error": "Choose a readable .onnx model file."})
        return 2
    try:
        import onnxruntime as ort
    except Exception:
        emit(
            {
                "ok": True,
                "validated": False,
                "label": path.stem,
                "warning": "ONNX Runtime is not installed, so this model will be validated when wake-word support starts.",
            }
        )
        return 0
    try:
        options = ort.SessionOptions()
        options.log_severity_level = 3
        session = ort.InferenceSession(
            str(path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        if not session.get_inputs() or not session.get_outputs():
            raise ValueError("the model has no inputs or outputs")
    except Exception:
        emit({"ok": False, "error": "That file is not a compatible ONNX wake-word model."})
        return 2

    try:
        from openwakeword.model import Model
    except Exception:
        emit(
            {
                "ok": True,
                "validated": False,
                "label": path.stem,
                "warning": "OpenWakeWord is not installed, so model compatibility will be checked when wake-word support starts.",
            }
        )
        return 0

    try:
        model = Model(wakeword_model_paths=[str(path)], vad_threshold=0)
        labels = sorted(str(label) for label in getattr(model, "models", {}))
        if not labels:
            raise ValueError("the model exposed no wake-word labels")
    except Exception:
        emit({"ok": False, "error": "That ONNX file could not be loaded by OpenWakeWord."})
        return 2

    emit({"ok": True, "validated": True, "label": labels[0], "warning": ""})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
