"""Wake-word model discovery and management for the Home Suite console."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from config_editor import ConfigEditError, ConfigEditor


MAX_WAKEWORD_MODEL_BYTES = 24 * 1024 * 1024
MAX_ACTIVE_WAKEWORD_MODELS = 20


class ConsoleWakewordError(RuntimeError):
    """A safe user-facing wake-word management error."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = int(status)


def _friendly_name(stem: str) -> str:
    words = re.sub(r"[_-]+", " ", str(stem or "")).strip()
    return words.title() if words else "Wake word"


def _model_id(path: Path) -> str:
    digest = hashlib.sha256(str(path.resolve(strict=False)).encode("utf-8")).hexdigest()
    return f"model-{digest[:20]}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_slug(filename: str) -> str:
    stem = Path(filename).stem
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", stem).strip("-._").lower()
    return slug[:64] or "wakeword"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def probe_onnx_model(path: Path, *, root: Path, timeout_seconds: float = 20.0) -> dict[str, Any]:
    """Validate one uploaded ONNX file in a bounded subprocess."""
    script = Path(root) / "tools" / "inspect_wakeword_model.py"
    if not script.is_file():
        return {
            "validated": False,
            "label": path.stem,
            "warning": "The local model validator is unavailable.",
        }
    try:
        completed = subprocess.run(
            [sys.executable, str(script), str(path)],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
            timeout=max(1.0, float(timeout_seconds)),
        )
    except subprocess.TimeoutExpired as exc:
        raise ConsoleWakewordError(
            "The model took too long to validate and was not added.",
            status=422,
        ) from exc
    try:
        payload = json.loads((completed.stdout or "").strip())
    except Exception:
        payload = {}
    if completed.returncode != 0 or not payload.get("ok"):
        raise ConsoleWakewordError(
            str(payload.get("error") or "That file is not a compatible ONNX wake-word model."),
            status=422,
        )
    return {
        "validated": bool(payload.get("validated")),
        "label": str(payload.get("label") or path.stem),
        "warning": str(payload.get("warning") or ""),
    }


class ConsoleWakewordManager:
    """Expose installed models and apply model selections through ConfigEditor."""

    def __init__(
        self,
        *,
        root: Path,
        editor: ConfigEditor,
        app_config=None,
        model_dir: Optional[Path] = None,
        model_probe: Optional[Callable[[Path], dict[str, Any]]] = None,
        extra_model_dirs: Optional[Iterable[Path]] = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.editor = editor
        self.app_config = app_config or editor.app_config
        self.model_dir = Path(model_dir or (self.root / "wake_models")).resolve()
        self.model_probe = model_probe or (
            lambda path: probe_onnx_model(path, root=self.root)
        )
        home = Path.home()
        default_extra_dirs = (
            home / "wake_models",
            home / "homesuite" / "wake_models",
            home / "piphone" / "wake_models",
        )
        self._extra_model_dirs = {
            Path(path).resolve(strict=False)
            for path in (default_extra_dirs if extra_model_dirs is None else extra_model_dirs)
        }
        self._lock = threading.RLock()
        self._known_directories: set[Path] = {self.model_dir}

    def create_upload_path(self) -> Path:
        self.model_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.model_dir, 0o700)
        descriptor, raw_path = tempfile.mkstemp(
            prefix=".wakeword-upload-",
            suffix=".onnx",
            dir=self.model_dir,
        )
        os.close(descriptor)
        path = Path(raw_path)
        os.chmod(path, 0o600)
        return path

    def _configured_paths(self) -> list[Path]:
        paths: list[Path] = []
        for raw in getattr(self.app_config, "WAKEWORD_MODEL_PATHS", []) or []:
            value = os.path.expandvars(os.path.expanduser(str(raw).strip()))
            if not value:
                continue
            path = Path(value)
            if not path.is_absolute():
                path = self.root / path
            resolved = path.resolve(strict=False)
            paths.append(resolved)
            self._known_directories.add(resolved.parent)
        return paths

    def _search_directories(self, configured: list[Path]) -> list[Path]:
        candidates = {
            self.model_dir,
            *self._extra_model_dirs,
            *(path.parent for path in configured),
            *self._known_directories,
        }
        return sorted(
            {path.resolve(strict=False) for path in candidates if path.is_dir()},
            key=lambda path: str(path).lower(),
        )

    def _metadata_for(self, path: Path) -> dict[str, Any]:
        if path.parent.resolve(strict=False) != self.model_dir:
            return {}
        return _read_json(path.with_suffix(".json"))

    def _model_row(self, path: Path, *, selected: bool) -> dict[str, Any]:
        path = path.resolve(strict=False)
        metadata = self._metadata_for(path)
        managed = (
            path.parent == self.model_dir
            and metadata.get("managed_by") == "homesuite-console"
        )
        exists = path.is_file()
        label = str(metadata.get("label") or path.stem)
        display_name = str(metadata.get("display_name") or _friendly_name(label))
        validation = str(metadata.get("validation") or ("local" if exists else "missing"))
        return {
            "id": _model_id(path),
            "display_name": display_name,
            "label": label,
            "filename": path.name,
            "path": str(path),
            "source": "Uploaded" if managed else "Local file",
            "selected": bool(selected),
            "exists": exists,
            "managed": managed,
            "removable": bool(managed and exists and not selected),
            "validation": validation,
            "validation_warning": str(metadata.get("validation_warning") or ""),
            "size_bytes": path.stat().st_size if exists else None,
        }

    def public_state(self) -> dict[str, Any]:
        with self._lock:
            configured = self._configured_paths()
            configured_set = set(configured)
            candidates: set[Path] = set(configured)
            for directory in self._search_directories(configured):
                try:
                    candidates.update(path.resolve(strict=False) for path in directory.glob("*.onnx"))
                except OSError:
                    continue

            models = [
                self._model_row(path, selected=path in configured_set)
                for path in candidates
            ]
            models.sort(
                key=lambda row: (
                    not row["selected"],
                    not row["exists"],
                    str(row["display_name"]).lower(),
                    str(row["filename"]).lower(),
                )
            )

            selected_label = str(getattr(self.app_config, "WAKEWORD_MODEL", "") or "").strip()
            if not configured and selected_label:
                models.insert(
                    0,
                    {
                        "id": f"builtin-{hashlib.sha256(selected_label.encode('utf-8')).hexdigest()[:20]}",
                        "display_name": _friendly_name(selected_label),
                        "label": selected_label,
                        "filename": "Bundled with OpenWakeWord",
                        "path": "",
                        "source": "OpenWakeWord built-in",
                        "selected": True,
                        "exists": True,
                        "managed": False,
                        "removable": False,
                        "validation": "bundled",
                        "validation_warning": "",
                        "size_bytes": None,
                    },
                )

            selected = [row["id"] for row in models if row["selected"]]
            enabled = bool(getattr(self.app_config, "WAKEWORD_ENABLED", False))
            return {
                "schema_version": 1,
                "enabled": enabled,
                "engine": str(getattr(self.app_config, "WAKEWORD_ENGINE", "openwakeword") or "openwakeword"),
                "threshold": float(getattr(self.app_config, "WAKEWORD_THRESHOLD", 0.5) or 0.5),
                "selected_ids": selected,
                "selected_count": len(selected),
                "listening_count": len(selected) if enabled else 0,
                "multiple_allowed": True,
                "max_active_models": MAX_ACTIVE_WAKEWORD_MODELS,
                "models": models,
                "upload": {
                    "accepted_extensions": [".onnx"],
                    "max_bytes": MAX_WAKEWORD_MODEL_BYTES,
                    "model_dir": str(self.model_dir),
                },
            }

    def _selection_changes(self, *, active_ids: Any, enabled: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not isinstance(active_ids, list):
            raise ConsoleWakewordError("Choose wake-word models from the available list.")
        normalized_ids = [str(value or "").strip() for value in active_ids]
        if any(not value for value in normalized_ids) or len(set(normalized_ids)) != len(normalized_ids):
            raise ConsoleWakewordError("The wake-word selection is invalid.")
        if len(normalized_ids) > MAX_ACTIVE_WAKEWORD_MODELS:
            raise ConsoleWakewordError(
                f"Select no more than {MAX_ACTIVE_WAKEWORD_MODELS} wake-word models on one device."
            )
        if not isinstance(enabled, bool):
            raise ConsoleWakewordError("Wake-word listening must be enabled or disabled.")

        state = self.public_state()
        by_id = {row["id"]: row for row in state["models"]}
        unknown = [model_id for model_id in normalized_ids if model_id not in by_id]
        if unknown:
            raise ConsoleWakewordError("The available wake-word models changed. Refresh and try again.", status=409)
        selected_rows = [by_id[model_id] for model_id in normalized_ids]
        missing = [row["display_name"] for row in selected_rows if not row["exists"]]
        if missing:
            raise ConsoleWakewordError(
                f"{missing[0]} is missing from this device and cannot be activated."
            )
        if enabled and not selected_rows:
            raise ConsoleWakewordError("Select at least one wake word before enabling listening.")

        builtins = [row for row in selected_rows if not row["path"]]
        custom = [row for row in selected_rows if row["path"]]
        if len(builtins) > 1 or (builtins and custom):
            raise ConsoleWakewordError(
                "Built-in and local wake-word models cannot be combined in this version."
            )

        model_paths = [row["path"] for row in custom]
        selected_label = builtins[0]["label"] if builtins else ""
        changes = [
            {"key": "WAKEWORD_ENABLED", "action": "set", "value": enabled},
            {"key": "WAKEWORD_MODEL_PATHS", "action": "set", "value": model_paths},
            {"key": "WAKEWORD_MODEL", "action": "set", "value": selected_label},
        ]
        selection = {
            "enabled": enabled,
            "active_ids": normalized_ids,
            "active_count": len(normalized_ids),
        }
        return changes, selection

    def preview(self, *, active_ids: Any, enabled: Any) -> dict[str, Any]:
        with self._lock:
            changes, selection = self._selection_changes(active_ids=active_ids, enabled=enabled)
            try:
                payload = self.editor.preview(changes)
            except ConfigEditError as exc:
                raise ConsoleWakewordError(str(exc), status=exc.status) from exc
            return {**payload, "selection": selection}

    def apply(self, *, active_ids: Any, enabled: Any, revisions: Any) -> dict[str, Any]:
        with self._lock:
            changes, selection = self._selection_changes(active_ids=active_ids, enabled=enabled)
            try:
                payload = self.editor.apply(changes, revisions)
            except ConfigEditError as exc:
                raise ConsoleWakewordError(str(exc), status=exc.status) from exc
            return {**payload, "selection": selection}

    def install_uploaded_file(self, temporary: Path, original_filename: str) -> dict[str, Any]:
        with self._lock:
            filename = Path(str(original_filename or "")).name
            if not filename or Path(filename).suffix.lower() != ".onnx":
                raise ConsoleWakewordError("Choose an OpenWakeWord .onnx model file.")
            temporary = Path(temporary)
            try:
                size = temporary.stat().st_size
            except OSError as exc:
                raise ConsoleWakewordError("The uploaded model could not be read.", status=500) from exc
            if size <= 0:
                raise ConsoleWakewordError("The uploaded model is empty.")
            if size > MAX_WAKEWORD_MODEL_BYTES:
                raise ConsoleWakewordError("The model exceeds the 24 MB upload limit.", status=413)

            probe = self.model_probe(temporary)
            digest = _sha256_file(temporary)
            slug = _safe_slug(filename)
            target = self.model_dir / f"{slug}.onnx"
            already_present = False
            if target.is_file():
                existing_digest = _sha256_file(target)
                if existing_digest == digest:
                    already_present = True
                else:
                    target = self.model_dir / f"{slug}-{digest[:10]}.onnx"
                    already_present = target.is_file() and _sha256_file(target) == digest
            if already_present:
                temporary.unlink(missing_ok=True)
            else:
                os.replace(temporary, target)
                os.chmod(target, 0o600)

            label = target.stem
            metadata = {
                "managed_by": "homesuite-console",
                "display_name": _friendly_name(Path(filename).stem),
                "label": label,
                "original_filename": filename,
                "sha256": digest,
                "size_bytes": size,
                "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "validation": "verified" if probe.get("validated") else "unverified",
                "validation_warning": str(probe.get("warning") or ""),
            }
            metadata_path = target.with_suffix(".json")
            existing_metadata = _read_json(metadata_path)
            if not already_present or existing_metadata.get("managed_by") == "homesuite-console":
                _atomic_json(metadata_path, metadata)
            row = self._model_row(target, selected=False)
            return {
                "added": not already_present,
                "model": row,
                "message": (
                    f"{row['display_name']} is ready to select."
                    if not already_present
                    else f"{row['display_name']} was already available."
                ),
            }

    def remove(self, model_id: str) -> dict[str, Any]:
        with self._lock:
            state = self.public_state()
            row = next((item for item in state["models"] if item["id"] == model_id), None)
            if row is None:
                raise ConsoleWakewordError("That wake-word model is no longer available.", status=404)
            if row["selected"]:
                raise ConsoleWakewordError("Deactivate this wake word before removing its file.", status=409)
            if not row["managed"] or not row["path"]:
                raise ConsoleWakewordError("Only models uploaded through Home Suite can be removed here.", status=403)
            path = Path(row["path"]).resolve(strict=False)
            if path.parent != self.model_dir:
                raise ConsoleWakewordError("That model is outside the managed wake-word directory.", status=403)
            path.unlink(missing_ok=True)
            path.with_suffix(".json").unlink(missing_ok=True)
            return {"removed": True, "model_id": model_id, "display_name": row["display_name"]}
