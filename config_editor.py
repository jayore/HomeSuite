"""Validated, atomic edits for the Home Suite management console."""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import os
import pprint
import re
import shutil
import stat
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

from config_inventory import build_config_inventory
from config_schema import (
    EDITABLE_FIELDS,
    LOCAL_PREFS_FILE,
    PRIVATE_CONFIG_FILE,
    ConfigField,
    field_for_key,
)


MANAGED_MARKER = "# Home Suite Console managed settings"

_BUTTON_GESTURE_ALIASES = {
    "press": "press",
    "single_press": "press",
    "single": "press",
    "double_press": "double_press",
    "double": "double_press",
    "long_press": "long_press",
    "long": "long_press",
    "hold": "long_press",
}


class ConfigEditError(RuntimeError):
    """A safe user-facing configuration error with an HTTP status."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = int(status)


@dataclass(frozen=True)
class NormalizedChange:
    field: ConfigField
    action: str
    value: Any = None


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(child) for child in value]
    return str(value)


def _button_commands(action: Any) -> list[str]:
    """Return executable commands from one configured gesture action."""
    if isinstance(action, str):
        command = action.strip()
        return [command] if command else []
    if isinstance(action, (list, tuple)):
        commands: list[str] = []
        for item in action:
            commands.extend(_button_commands(item))
        return commands
    if isinstance(action, dict):
        if "command" in action:
            return _button_commands(action.get("command"))
        if "commands" in action:
            return _button_commands(action.get("commands"))
    return []


def _normalize_button_map_keys(field: ConfigField, value: dict[Any, Any]) -> dict[int, Any]:
    """Keep Python config output readable while accepting JSON string keys."""
    normalized: dict[int, Any] = {}
    for raw_button, child in value.items():
        if isinstance(raw_button, bool):
            raise ConfigEditError(f"{field.label} button IDs must be whole numbers.")
        try:
            button = int(raw_button)
        except (TypeError, ValueError) as exc:
            raise ConfigEditError(f"{field.label} button IDs must be whole numbers.") from exc
        if button < 1:
            raise ConfigEditError(f"{field.label} button IDs must be 1 or greater.")
        if button in normalized:
            raise ConfigEditError(f"{field.label} contains button {button} more than once.")

        if field.key == "PHYSICAL_BUTTON_PINS":
            if isinstance(child, bool):
                raise ConfigEditError("Each command-button GPIO pin must be a whole number.")
            try:
                child = int(child)
            except (TypeError, ValueError) as exc:
                raise ConfigEditError("Each command-button GPIO pin must be a whole number.") from exc
        normalized[button] = child
    return normalized


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _assignment_nodes(tree: ast.Module) -> dict[str, list[tuple[ast.stmt, ast.expr]]]:
    rows: dict[str, list[tuple[ast.stmt, ast.expr]]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            rows.setdefault(node.targets[0].id, []).append((node, node.value))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            rows.setdefault(node.target.id, []).append((node, node.value))
    return rows


def _literal(value_node: Optional[ast.expr]) -> Any:
    if value_node is None:
        return None
    try:
        return ast.literal_eval(value_node)
    except Exception:
        return None


def source_revision(source: str) -> str:
    """Return the revision token used by console review/apply workflows."""
    return _digest(source)


def parse_config_source(
    filename: str,
    source: str,
) -> tuple[ast.Module, dict[str, list[tuple[ast.stmt, ast.expr]]]]:
    """Parse a Python config file and index its top-level literal assignments."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        raise ConfigEditError(
            f"{filename} has a syntax error at line {exc.lineno}; fix it before using the console editor.",
            status=409,
        ) from exc
    return tree, _assignment_nodes(tree)


def rewrite_config_assignments(
    filename: str,
    source: str,
    *,
    updates: dict[str, Any],
    removals: Iterable[str] = (),
    marker: str = MANAGED_MARKER,
    sort_dicts: bool = True,
) -> str:
    """Safely replace, append, or remove top-level Python assignments."""
    tree, assignments = parse_config_source(filename, source)
    del tree
    remove_keys = {str(key) for key in removals}
    overlap = remove_keys.intersection(updates)
    if overlap:
        raise ConfigEditError(
            f"Conflicting edits were requested for {sorted(overlap)[0]}.",
            status=409,
        )

    encoded = source.encode("utf-8")
    lines = encoded.splitlines(keepends=True)
    starts: list[int] = []
    cursor = 0
    for line in lines:
        starts.append(cursor)
        cursor += len(line)

    def offset(lineno: int, column: int) -> int:
        if lineno < 1 or lineno > len(starts):
            raise ConfigEditError(
                f"Could not safely locate an assignment in {filename}.",
                status=409,
            )
        return starts[lineno - 1] + column

    replacements: list[tuple[int, int, bytes]] = []
    appended: list[str] = []
    for key in remove_keys:
        for statement, _value_node in assignments.get(key) or []:
            start = starts[statement.lineno - 1]
            end = starts[statement.end_lineno] if statement.end_lineno < len(starts) else len(encoded)
            replacements.append((start, end, b""))

    for key, value in updates.items():
        nodes = assignments.get(key) or []
        rendered = pprint.pformat(
            value,
            width=88,
            compact=False,
            sort_dicts=sort_dicts,
        ).encode("utf-8")
        if nodes:
            _statement, value_node = nodes[-1]
            replacements.append(
                (
                    offset(value_node.lineno, value_node.col_offset),
                    offset(value_node.end_lineno, value_node.end_col_offset),
                    rendered,
                )
            )
        else:
            appended.append(f"{key} = {rendered.decode('utf-8')}")

    ordered = sorted(replacements, key=lambda item: (item[0], item[1]))
    for previous, current in zip(ordered, ordered[1:]):
        if current[0] < previous[1]:
            raise ConfigEditError(
                f"Overlapping assignments in {filename} cannot be edited safely.",
                status=409,
            )
    for start, end, replacement in reversed(ordered):
        encoded = encoded[:start] + replacement + encoded[end:]

    rewritten = encoded.decode("utf-8")
    if appended:
        if rewritten and not rewritten.endswith("\n"):
            rewritten += "\n"
        if marker and marker not in rewritten:
            rewritten += f"\n{marker}\n"
        elif not rewritten.endswith("\n"):
            rewritten += "\n"
        rewritten += "\n".join(appended) + "\n"

    try:
        compile(rewritten, filename, "exec")
    except SyntaxError as exc:
        raise ConfigEditError(
            f"The proposed {filename} update did not compile; nothing was written.",
            status=409,
        ) from exc
    return rewritten


def atomic_write_config(path: Path, content: str) -> None:
    """Atomically replace one config file while retaining its file mode."""
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    temporary: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f".{path.name}.console-",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class ConfigEditor:
    """Expose schema state and apply allowlisted edits transactionally."""

    def __init__(
        self,
        *,
        root: Path,
        app_config=None,
        private_config=None,
        backup_root: Optional[Path] = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.app_config = app_config or importlib.import_module("app_config")
        self.private_config = private_config or importlib.import_module("private_config")
        self.backup_root = Path(backup_root or (self.root / "backups" / "console"))
        self._lock = threading.RLock()

    def _path(self, filename: str) -> Path:
        if filename not in {LOCAL_PREFS_FILE, PRIVATE_CONFIG_FILE}:
            raise ConfigEditError("That configuration target is not editable.")
        path = self.root / filename
        if not path.exists():
            raise ConfigEditError(f"{filename} does not exist on this node.", status=500)
        return path

    def _resolved_path(self, filename: str) -> Path:
        # Following a deployment symlink keeps an isolated console checkout from
        # replacing the link itself when it edits the node's real config file.
        return self._path(filename).resolve(strict=True)

    def _read_sources(self) -> dict[str, str]:
        return {
            filename: self._path(filename).read_text(encoding="utf-8")
            for filename in (LOCAL_PREFS_FILE, PRIVATE_CONFIG_FILE)
        }

    @staticmethod
    def _parse(filename: str, source: str) -> tuple[ast.Module, dict[str, list[tuple[ast.stmt, ast.expr]]]]:
        return parse_config_source(filename, source)

    def _effective_value(
        self,
        field: ConfigField,
        assignments: dict[str, list[tuple[ast.stmt, ast.expr]]],
    ) -> Any:
        nodes = assignments.get(field.key) or []
        if nodes:
            value = _literal(nodes[-1][1])
            if value is not None:
                return value
            module = self.private_config if field.target_file == PRIVATE_CONFIG_FILE else self.app_config
            return getattr(module, field.key, None)
        if field.target_file == LOCAL_PREFS_FILE:
            return self._inherited_value(field.key)
        return getattr(self.private_config, field.key, None)

    def _inherited_value(self, key: str) -> Any:
        """Read the deployment/default value without reusing a local override."""
        for filename in ("deployment_config.py", "app_config.py"):
            path = self.root / filename
            if not path.exists():
                continue
            try:
                source = path.read_text(encoding="utf-8")
                assignments = self._parse(filename, source)[1]
                nodes = assignments.get(key) or []
                if nodes:
                    value = _literal(nodes[-1][1])
                    if value is not None:
                        return value
            except ConfigEditError:
                raise
            except Exception:
                continue
        local_keys = set(getattr(self.app_config, "LOCAL_PREFS_KEYS", ()) or ())
        if key in local_keys:
            return None
        return getattr(self.app_config, key, None)

    def _source_label(
        self,
        field: ConfigField,
        assignments: dict[str, list[tuple[ast.stmt, ast.expr]]],
    ) -> str:
        if field.target_file == PRIVATE_CONFIG_FILE:
            return "private"
        if assignments.get(field.key):
            return "device"
        deployment_keys = set(getattr(self.app_config, "DEPLOYMENT_CONFIG_KEYS", ()) or ())
        return "deployment" if field.key in deployment_keys else "default"

    def _choices(self, field: ConfigField) -> list[dict[str, str]]:
        choices = [{"value": value, "label": label} for value, label in field.choices]
        if field.dynamic_choices in {"rooms", "rooms_optional"}:
            if field.dynamic_choices == "rooms_optional":
                choices.append({"value": "", "label": "Automatic"})
            rooms = getattr(self.app_config, "ROOMS", {}) or {}
            for room_id, room in rooms.items():
                if not isinstance(room, dict):
                    continue
                label = str(room.get("label") or str(room_id).replace("_", " ").title())
                choices.append({"value": str(room_id), "label": label})
        return choices

    def public_state(self, *, include_secrets: bool = False) -> dict:
        """Return editable metadata and current values for the console surface.

        Normal read requests stay redacted. The authenticated edit endpoint may
        opt into secret values so existing credentials can populate masked
        controls instead of behaving like write-only fields.
        """
        with self._lock:
            sources = self._read_sources()
            parsed = {
                filename: self._parse(filename, source)[1]
                for filename, source in sources.items()
            }
            sections: list[dict[str, Any]] = []
            section_map: dict[str, dict[str, Any]] = {}
            fields: list[dict[str, Any]] = []
            for field in EDITABLE_FIELDS:
                assignments = parsed[field.target_file]
                current = self._effective_value(field, assignments)
                configured = _has_value(current)
                if field.key == "HOMESUITE_HTTP_API_KEY" and not configured:
                    legacy_value = getattr(self.private_config, "PIPHONE_HTTP_API_KEY", "")
                    configured = _has_value(legacy_value)
                    if configured and include_secrets:
                        current = legacy_value
                row = {
                    "key": field.key,
                    "section": field.section,
                    "label": field.label,
                    "description": field.description,
                    "placeholder": field.placeholder,
                    "help_text": field.help_text,
                    "docs_path": field.docs_path,
                    "type": field.value_type,
                    "required": field.required,
                    "secret": field.secret,
                    "configured": configured,
                    "value": (
                        None
                        if field.secret and not include_secrets
                        else _json_safe(current)
                    ),
                    "source": self._source_label(field, assignments),
                    "target_file": field.target_file,
                    "can_reset": (
                        field.target_file == LOCAL_PREFS_FILE
                        and bool(assignments.get(field.key))
                        and (not field.required or _has_value(self._inherited_value(field.key)))
                    ),
                    "can_clear": field.target_file == PRIVATE_CONFIG_FILE and not field.required and configured,
                    "choices": self._choices(field),
                    "minimum": field.minimum,
                    "maximum": field.maximum,
                    "restart_services": list(field.restart_services),
                }
                fields.append(row)
                section = section_map.get(field.section)
                if section is None:
                    section = {
                        "id": field.section,
                        "label": field.section_label,
                        "field_keys": [],
                        "optional": field.section not in {
                            "node",
                            "ptt",
                            "wakeword",
                            "audio",
                            "assistant",
                            "network",
                            "core_services",
                            "network_credentials",
                        },
                    }
                    section_map[field.section] = section
                    sections.append(section)
                section["field_keys"].append(field.key)
            return {
                "schema_version": 1,
                "sections": sections,
                "fields": fields,
                "inventory": build_config_inventory(
                    root=self.root,
                    app_config=self.app_config,
                    private_config=self.private_config,
                ),
                "revisions": {filename: _digest(source) for filename, source in sources.items()},
            }

    @staticmethod
    def _split_values(value: Any) -> list[Any]:
        if isinstance(value, (list, tuple)):
            return list(value)
        if isinstance(value, str):
            return [part.strip() for part in re.split(r"[\n,]+", value) if part.strip()]
        raise ConfigEditError("Enter one value per line.")

    def _normalize_value(self, field: ConfigField, value: Any) -> Any:
        kind = field.value_type
        if kind == "boolean":
            if not isinstance(value, bool):
                raise ConfigEditError(f"{field.label} must be enabled or disabled.")
            normalized: Any = value
        elif kind == "integer":
            if isinstance(value, bool):
                raise ConfigEditError(f"{field.label} must be a whole number.")
            try:
                normalized = int(value)
            except (TypeError, ValueError) as exc:
                raise ConfigEditError(f"{field.label} must be a whole number.") from exc
        elif kind == "number":
            if isinstance(value, bool):
                raise ConfigEditError(f"{field.label} must be a number.")
            try:
                normalized = float(value)
            except (TypeError, ValueError) as exc:
                raise ConfigEditError(f"{field.label} must be a number.") from exc
        elif kind == "list_integer":
            values = self._split_values(value)
            normalized = []
            for item in values:
                if isinstance(item, bool):
                    raise ConfigEditError(f"{field.label} accepts numeric IDs only.")
                try:
                    normalized.append(int(item))
                except (TypeError, ValueError) as exc:
                    raise ConfigEditError(f"{field.label} accepts numeric IDs only.") from exc
        elif kind == "list_string":
            normalized = [str(item).strip() for item in self._split_values(value) if str(item).strip()]
        elif kind == "json_object":
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError as exc:
                    raise ConfigEditError(f"{field.label} must be a valid JSON object.") from exc
            if not isinstance(value, dict):
                raise ConfigEditError(f"{field.label} must be a JSON object.")
            normalized = (
                _normalize_button_map_keys(field, value)
                if field.key in {"PHYSICAL_BUTTON_PINS", "PHYSICAL_BUTTON_ACTIONS"}
                else value
            )
        else:
            normalized = str(value or "").strip()

        empty = not _has_value(normalized)
        if empty and (field.required or not field.allow_empty):
            raise ConfigEditError(f"{field.label} cannot be empty.")

        if kind == "choice" and not empty:
            allowed = {choice[0] for choice in field.choices}
            if field.dynamic_choices in {"rooms", "rooms_optional"}:
                allowed.update(str(key) for key in (getattr(self.app_config, "ROOMS", {}) or {}))
            if normalized not in allowed:
                raise ConfigEditError(f"Choose a valid value for {field.label}.")

        if kind == "url" and not empty:
            parsed = urlsplit(normalized)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ConfigEditError(f"{field.label} must be a complete http:// or https:// URL.")

        if field.pattern and not empty and not re.fullmatch(field.pattern, str(normalized)):
            raise ConfigEditError(f"{field.label} has an invalid format.")

        if field.minimum is not None and isinstance(normalized, (int, float)) and normalized < field.minimum:
            raise ConfigEditError(f"{field.label} must be at least {field.minimum:g}.")
        if field.maximum is not None and isinstance(normalized, (int, float)) and normalized > field.maximum:
            raise ConfigEditError(f"{field.label} must be at most {field.maximum:g}.")
        return normalized

    def _normalize_changes(self, changes: Any) -> list[NormalizedChange]:
        if not isinstance(changes, list) or not changes:
            raise ConfigEditError("Choose at least one setting to change.")
        if len(changes) > len(EDITABLE_FIELDS):
            raise ConfigEditError("Too many settings were submitted.")
        seen: set[str] = set()
        normalized: list[NormalizedChange] = []
        for raw in changes:
            if not isinstance(raw, dict):
                raise ConfigEditError("Each configuration change must be an object.")
            key = str(raw.get("key") or "").strip()
            field = field_for_key(key)
            if field is None:
                raise ConfigEditError(f"{key or 'Unknown setting'} is not editable from the console.")
            if key in seen:
                raise ConfigEditError(f"{field.label} was submitted more than once.")
            seen.add(key)
            action = str(raw.get("action") or "set").strip().lower()
            if action == "reset":
                if field.target_file != LOCAL_PREFS_FILE:
                    raise ConfigEditError(f"{field.label} cannot be reset to a device default.")
                if field.required and not _has_value(self._inherited_value(field.key)):
                    raise ConfigEditError(f"{field.label} has no inherited value and cannot be reset.")
                normalized.append(NormalizedChange(field=field, action=action))
                continue
            if action == "clear":
                if field.target_file != PRIVATE_CONFIG_FILE or field.required:
                    raise ConfigEditError(f"{field.label} cannot be cleared.")
                empty_value = [] if field.value_type.startswith("list_") else ""
                normalized.append(NormalizedChange(field=field, action=action, value=empty_value))
                continue
            if action != "set":
                raise ConfigEditError(f"Unsupported action for {field.label}.")
            value = self._normalize_value(field, raw.get("value"))
            if field.secret and not _has_value(value):
                raise ConfigEditError(f"Enter a new value for {field.label}, or use Clear.")
            normalized.append(NormalizedChange(field=field, action=action, value=value))
        return normalized

    def _current_context(self, sources: dict[str, str]) -> dict[str, dict[str, Any]]:
        context: dict[str, dict[str, Any]] = {}
        parsed = {
            filename: self._parse(filename, source)[1]
            for filename, source in sources.items()
        }
        for field in EDITABLE_FIELDS:
            assignments = parsed[field.target_file]
            context[field.key] = {
                "value": self._effective_value(field, assignments),
                "source": self._source_label(field, assignments),
                "assigned": bool(assignments.get(field.key)),
            }
        return context

    def _cross_validate(
        self,
        changes: Iterable[NormalizedChange],
        context: dict[str, dict[str, Any]],
    ) -> None:
        by_key = {change.field.key: change for change in changes}

        def resulting(key: str) -> Any:
            change = by_key.get(key)
            if change is None:
                return context.get(key, {}).get("value")
            if change.action in {"set", "clear"}:
                return change.value
            return self._inherited_value(key)

        if {"HOMESUITE_HTTP_API_KEY", "HOMESUITE_CONSOLE_KEY"}.intersection(by_key):
            legacy_api_key = getattr(self.private_config, "PIPHONE_HTTP_API_KEY", "")
            if (
                not _has_value(resulting("HOMESUITE_HTTP_API_KEY"))
                and not _has_value(resulting("HOMESUITE_CONSOLE_KEY"))
                and not _has_value(legacy_api_key)
            ):
                raise ConfigEditError(
                    "The companion API key and console passphrase cannot both be empty; the console would fail closed."
                )

        if "UNIFIED_SERVER_PORT" in by_key:
            console_port = int(getattr(self.app_config, "CONSOLE_PORT", 8766) or 8766)
            if int(resulting("UNIFIED_SERVER_PORT")) == console_port:
                raise ConfigEditError("The companion API and management console cannot use the same port.")

        satellite_keys = {
            "COMMAND_PROCESSING_MODE",
            "SATELLITE_BRAIN_URL",
            "SATELLITE_BRAIN_API_KEY",
            "HOMESUITE_HTTP_API_KEY",
        }
        if satellite_keys.intersection(by_key) and resulting("COMMAND_PROCESSING_MODE") == "satellite":
            if not _has_value(resulting("SATELLITE_BRAIN_URL")):
                raise ConfigEditError("Enter a brain URL before enabling satellite command processing.")
            if not (
                _has_value(resulting("SATELLITE_BRAIN_API_KEY"))
                or _has_value(resulting("HOMESUITE_HTTP_API_KEY"))
            ):
                raise ConfigEditError(
                    "Satellite command processing requires the brain API key or a shared companion API key."
                )

        gpio_keys = {
            "PTT_ENABLED",
            "PTT_GPIO_PIN",
            "PHYSICAL_BUTTONS_ENABLED",
            "PHYSICAL_BUTTON_PINS",
            "PHYSICAL_BUTTON_ACTIONS",
        }
        if gpio_keys.intersection(by_key):
            raw_pins = resulting("PHYSICAL_BUTTON_PINS") or {}
            raw_actions = resulting("PHYSICAL_BUTTON_ACTIONS") or {}
            if not isinstance(raw_pins, dict) or not isinstance(raw_actions, dict):
                raise ConfigEditError("GPIO button pins and actions must be JSON objects.")

            pins: dict[int, int] = {}
            for raw_button, raw_pin in raw_pins.items():
                try:
                    button = int(raw_button)
                    pin = int(raw_pin)
                except (TypeError, ValueError) as exc:
                    raise ConfigEditError("Each command button and GPIO pin must be a whole number.") from exc
                if button < 1:
                    raise ConfigEditError("Command button numbers must be 1 or greater.")
                if pin < 0 or pin > 27:
                    raise ConfigEditError("Command-button BCM GPIO pins must be between 0 and 27.")
                if pin in pins.values():
                    raise ConfigEditError(f"BCM GPIO {pin} is assigned to more than one command button.")
                pins[button] = pin

            action_buttons: set[int] = set()
            for raw_button, gestures in raw_actions.items():
                try:
                    button = int(raw_button)
                except (TypeError, ValueError) as exc:
                    raise ConfigEditError("Each button action key must be a whole-number button ID.") from exc
                if button < 1:
                    raise ConfigEditError("Command button numbers must be 1 or greater.")
                if not isinstance(gestures, dict):
                    raise ConfigEditError(f"Actions for button {button} must be a JSON object.")
                seen_gestures: set[str] = set()
                for raw_gesture, action in gestures.items():
                    gesture_key = str(raw_gesture).strip().lower()
                    gesture = _BUTTON_GESTURE_ALIASES.get(gesture_key)
                    if gesture is None:
                        raise ConfigEditError(
                            f"Button {button} has unsupported gesture {raw_gesture!r}; "
                            "use press, double_press, or long_press."
                        )
                    if gesture in seen_gestures:
                        raise ConfigEditError(
                            f"Button {button} configures {gesture} more than once through gesture aliases."
                        )
                    seen_gestures.add(gesture)
                    if not _button_commands(action):
                        raise ConfigEditError(
                            f"Button {button} {gesture.replace('_', ' ')} needs at least one command phrase."
                        )
                    if isinstance(action, dict):
                        if "command" in action and "commands" in action:
                            raise ConfigEditError(
                                f"Button {button} {gesture.replace('_', ' ')} cannot define both command and commands."
                            )
                        if "repeat_interval_ms" in action:
                            try:
                                interval_ms = float(action["repeat_interval_ms"])
                            except (TypeError, ValueError) as exc:
                                raise ConfigEditError(
                                    f"Button {button} repeat interval must be a number of milliseconds."
                                ) from exc
                            if interval_ms < 50:
                                raise ConfigEditError(
                                    f"Button {button} repeat interval must be at least 50 milliseconds."
                                )
                        if "max_repeats" in action:
                            try:
                                max_repeats = int(action["max_repeats"])
                            except (TypeError, ValueError) as exc:
                                raise ConfigEditError(
                                    f"Button {button} maximum repeats must be a whole number."
                                ) from exc
                            if max_repeats < 1:
                                raise ConfigEditError(
                                    f"Button {button} maximum repeats must be 1 or greater."
                                )
                action_buttons.add(button)

            missing_pins = sorted(action_buttons.difference(pins))
            if missing_pins:
                joined = ", ".join(str(button) for button in missing_pins)
                raise ConfigEditError(f"Button actions {joined} do not have assigned GPIO pins.")

            if bool(resulting("PHYSICAL_BUTTONS_ENABLED")) and not pins:
                raise ConfigEditError("Assign at least one GPIO pin before enabling command buttons.")

            if bool(resulting("PTT_ENABLED")) and bool(resulting("PHYSICAL_BUTTONS_ENABLED")):
                ptt_pin = int(resulting("PTT_GPIO_PIN"))
                if ptt_pin in pins.values():
                    raise ConfigEditError(
                        f"BCM GPIO {ptt_pin} cannot be shared by PTT and a command button."
                    )

    @staticmethod
    def _display_value(value: Any) -> str:
        if value is None or value == "" or value == []:
            return "Not configured"
        if isinstance(value, bool):
            return "Enabled" if value else "Disabled"
        if isinstance(value, list):
            return ", ".join(str(item) for item in value) or "Not configured"
        return str(value)

    def _prepare(self, raw_changes: Any) -> tuple[list[NormalizedChange], dict[str, str], list[dict], list[str]]:
        changes = self._normalize_changes(raw_changes)
        sources = self._read_sources()
        context = self._current_context(sources)
        self._cross_validate(changes, context)

        effective: list[NormalizedChange] = []
        summaries: list[dict] = []
        restart_services: set[str] = set()
        for change in changes:
            current = context[change.field.key]
            if change.action == "reset" and not current["assigned"]:
                continue
            if change.action == "clear" and not _has_value(current["value"]):
                continue
            if change.action == "set" and current["value"] == change.value:
                continue
            effective.append(change)
            restart_services.update(change.field.restart_services)
            if change.field.secret:
                before = "Configured" if _has_value(current["value"]) else "Not configured"
                after = "Will be cleared" if change.action == "clear" else (
                    "Will be replaced" if _has_value(current["value"]) else "Will be configured"
                )
            else:
                before = self._display_value(current["value"])
                if change.action == "reset":
                    fallback = self._inherited_value(change.field.key)
                    after = f"{self._display_value(fallback)} (inherited)"
                else:
                    after = self._display_value(change.value)
            summaries.append(
                {
                    "key": change.field.key,
                    "label": change.field.label,
                    "target_file": change.field.target_file,
                    "action": change.action,
                    "secret": change.field.secret,
                    "before": before,
                    "after": after,
                    "restart_services": list(change.field.restart_services),
                }
            )
        return effective, sources, summaries, sorted(restart_services)

    def preview(self, changes: Any) -> dict:
        with self._lock:
            effective, sources, summaries, restart_services = self._prepare(changes)
            targets = sorted({change.field.target_file for change in effective})
            return {
                "changes": summaries,
                "change_count": len(summaries),
                "revisions": {target: _digest(sources[target]) for target in targets},
                "restart_services": restart_services,
            }

    @staticmethod
    def _render_value(value: Any) -> str:
        return pprint.pformat(value, width=88, compact=False, sort_dicts=True)

    def _rewrite(self, filename: str, source: str, changes: list[NormalizedChange]) -> str:
        updates = {
            change.field.key: change.value
            for change in changes
            if change.action != "reset"
        }
        removals = {
            change.field.key
            for change in changes
            if change.action == "reset"
        }
        return rewrite_config_assignments(
            filename,
            source,
            updates=updates,
            removals=removals,
        )

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        atomic_write_config(path, content)

    def apply(self, changes: Any, revisions: Any) -> dict:
        with self._lock:
            effective, sources, summaries, restart_services = self._prepare(changes)
            if not effective:
                return {
                    "applied": False,
                    "changes": [],
                    "change_count": 0,
                    "restart_services": [],
                    "written_files": [],
                    "backup_dir": None,
                }
            if not isinstance(revisions, dict):
                raise ConfigEditError("Review the changes again before applying them.", status=409)
            targets = sorted({change.field.target_file for change in effective})
            for target in targets:
                if revisions.get(target) != _digest(sources[target]):
                    raise ConfigEditError(
                        f"{target} changed after this review. Reload the editor and review again.",
                        status=409,
                    )

            candidates: dict[str, str] = {}
            for target in targets:
                target_changes = [change for change in effective if change.field.target_file == target]
                candidates[target] = self._rewrite(target, sources[target], target_changes)

            stamp = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
            backup_dir = self.backup_root / stamp
            try:
                self.backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.chmod(self.backup_root, 0o700)
                backup_dir.mkdir(mode=0o700, exist_ok=False)
                for target in targets:
                    shutil.copy2(self._resolved_path(target), backup_dir / target)
            except Exception as exc:
                raise ConfigEditError(
                    "Home Suite could not create a private configuration backup, so nothing was written.",
                    status=500,
                ) from exc

            written: list[str] = []
            try:
                for target in targets:
                    self._atomic_write(self._resolved_path(target), candidates[target])
                    written.append(target)
            except Exception as exc:
                for target in reversed(written):
                    try:
                        self._atomic_write(self._resolved_path(target), sources[target])
                    except Exception:
                        pass
                raise ConfigEditError(
                    "The configuration write failed and was rolled back. Check file permissions and logs.",
                    status=500,
                ) from exc

            return {
                "applied": True,
                "changes": summaries,
                "change_count": len(summaries),
                "restart_services": restart_services,
                "written_files": written,
                "backup_dir": str(backup_dir),
            }
