"""Persistent setup-completion and bounded runtime activation state."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any


SETUP_COMPLETE_MARKER = "setup_complete.json"
RUNTIME_PATH_UNIT = Path("/etc/systemd/system/homesuite-runtime.path")


class ConsoleSetupError(RuntimeError):
    """Safe user-facing setup failure with an HTTP status."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = int(status)


class ConsoleSetupManager:
    """Write only the fixed marker consumed by homesuite-runtime.path."""

    def __init__(
        self,
        *,
        root: Path,
        path_unit: Path = RUNTIME_PATH_UNIT,
    ) -> None:
        self.root = Path(root).resolve()
        self.marker_path = self.root / "state" / SETUP_COMPLETE_MARKER
        self.path_unit = Path(path_unit)
        self._lock = threading.RLock()

    def activation_requested(self) -> bool:
        return self.marker_path.is_file()

    def public_status(self, *, runtime_healthy: bool) -> dict[str, Any]:
        requested = self.activation_requested()
        return {
            "schema_version": 1,
            "complete": bool(requested or runtime_healthy),
            "activation_requested": requested,
            "activation_supported": self.path_unit.is_file(),
            "runtime_healthy": bool(runtime_healthy),
        }

    def record_running_installation(self) -> bool:
        """Persist completion for a healthy installation that predates setup."""
        with self._lock:
            if self.marker_path.is_file():
                return False
            self._write_marker(source="existing_runtime")
            return True

    def request_activation(self) -> dict[str, Any]:
        with self._lock:
            if self.marker_path.is_file():
                return {
                    "activation_requested": True,
                    "already_requested": True,
                }
            if not self.path_unit.is_file():
                raise ConsoleSetupError(
                    "The runtime activation helper is not installed on this node.",
                    status=409,
                )
            self._write_marker(source="activation_request")
            return {
                "activation_requested": True,
                "already_requested": False,
            }

    def _write_marker(self, *, source: str) -> None:
        self.marker_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "completed_at": time.time(),
            "source": str(source),
        }
        temporary = self.marker_path.with_name(
            f".{self.marker_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.marker_path)
        finally:
            temporary.unlink(missing_ok=True)
