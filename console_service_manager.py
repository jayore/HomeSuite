"""Track and perform fixed Home Suite service activation from the console."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional


RUNTIME_SERVICE = "homesuite.service"
CONSOLE_SERVICE = "homesuite-console.service"
MANAGED_SERVICES = (RUNTIME_SERVICE, CONSOLE_SERVICE)
SERVICE_LABELS = {
    RUNTIME_SERVICE: "Home Suite runtime",
    CONSOLE_SERVICE: "Management console",
}


class ConsoleServiceError(RuntimeError):
    def __init__(self, message: str, *, status: int = 500) -> None:
        super().__init__(message)
        self.status = int(status)


class ConsoleServiceManager:
    """Manage only Home Suite's two known systemd units without privilege escalation."""

    def __init__(
        self,
        *,
        root: Path,
        runner: Callable[..., Any] = subprocess.run,
        kill: Callable[[int, int], None] = os.kill,
        stat: Callable[[Any], Any] = os.stat,
        effective_uid: Callable[[], int] = os.geteuid,
    ) -> None:
        self.root = Path(root).resolve()
        self.state_path = self.root / "state" / "console_restart_required.json"
        self._runner = runner
        self._kill = kill
        self._stat = stat
        self._effective_uid = effective_uid
        self._lock = threading.RLock()

    @staticmethod
    def _service_name(service: Any) -> str:
        normalized = str(service or "").strip()
        if normalized not in MANAGED_SERVICES:
            raise ConsoleServiceError("Only Home Suite services can be restarted here.", status=400)
        return normalized

    def _systemd_properties(self, service: str) -> dict[str, str]:
        try:
            result = self._runner(
                [
                    "systemctl",
                    "show",
                    service,
                    "--property=LoadState,ActiveState,SubState,MainPID,Restart,InvocationID",
                    "--no-pager",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"_error": str(exc)}
        if int(result.returncode) != 0:
            return {"_error": str(result.stderr or result.stdout or "systemctl failed").strip()}
        properties: dict[str, str] = {}
        for line in str(result.stdout or "").splitlines():
            key, separator, value = line.partition("=")
            if separator:
                properties[key.strip()] = value.strip()
        return properties

    def service_status(self, service: Any) -> dict[str, Any]:
        name = self._service_name(service)
        properties = self._systemd_properties(name)
        error = properties.get("_error")
        try:
            main_pid = int(properties.get("MainPID") or 0)
        except (TypeError, ValueError):
            main_pid = 0
        same_user = False
        if main_pid > 1:
            try:
                same_user = int(self._stat(f"/proc/{main_pid}").st_uid) == int(self._effective_uid())
            except (OSError, TypeError, ValueError):
                same_user = False
        load_state = str(properties.get("LoadState") or "unknown")
        active_state = str(properties.get("ActiveState") or "unknown")
        restart_mode = str(properties.get("Restart") or "")
        restart_supported = bool(
            not error
            and load_state == "loaded"
            and active_state == "active"
            and main_pid > 1
            and same_user
            and restart_mode == "always"
        )
        unavailable_reason = None
        if error:
            unavailable_reason = "systemd status is unavailable"
        elif load_state != "loaded":
            unavailable_reason = "the systemd unit is not installed"
        elif active_state != "active" or main_pid <= 1:
            unavailable_reason = "the service is not currently running"
        elif not same_user:
            unavailable_reason = "the service runs as a different user"
        elif restart_mode != "always":
            unavailable_reason = "the service is not configured to restart automatically"
        return {
            "service": name,
            "label": SERVICE_LABELS[name],
            "load_state": load_state,
            "active_state": active_state,
            "sub_state": str(properties.get("SubState") or "unknown"),
            "main_pid": main_pid,
            "invocation_id": str(properties.get("InvocationID") or ""),
            "restart_mode": restart_mode,
            "same_user": same_user,
            "restart_supported": restart_supported,
            "unavailable_reason": unavailable_reason,
        }

    def _read_pending(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
        services = payload.get("services") if isinstance(payload, dict) else None
        if not isinstance(services, dict):
            return {}
        return {
            name: dict(value)
            for name, value in services.items()
            if name in MANAGED_SERVICES and isinstance(value, dict)
        }

    def _write_pending(self, services: dict[str, dict[str, Any]]) -> None:
        if not services:
            try:
                self.state_path.unlink(missing_ok=True)
            except OSError:
                pass
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "services": services,
        }
        temporary = self.state_path.with_name(
            f".{self.state_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.state_path)

    @staticmethod
    def _clean_reasons(reasons: Iterable[Any]) -> list[str]:
        cleaned: list[str] = []
        for reason in reasons:
            text = " ".join(str(reason or "").split())[:160]
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned[:12]

    def mark_required(self, services: Iterable[Any], reasons: Iterable[Any]) -> dict[str, dict[str, Any]]:
        requested = [self._service_name(service) for service in services if service in MANAGED_SERVICES]
        if not requested:
            return self.pending()
        clean_reasons = self._clean_reasons(reasons) or ["Saved configuration"]
        with self._lock:
            pending = self._read_pending()
            for service in requested:
                status = self.service_status(service)
                existing = pending.get(service, {})
                if existing.get("invocation_id") == status.get("invocation_id"):
                    merged = self._clean_reasons([*(existing.get("reasons") or []), *clean_reasons])
                else:
                    merged = list(clean_reasons)
                pending[service] = {
                    "required_at": time.time(),
                    "invocation_id": status.get("invocation_id") or "",
                    "reasons": merged,
                }
            self._write_pending(pending)
            return dict(pending)

    def pending(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return self._read_pending()

    def reconcile(self, service: Any, *, healthy: bool) -> bool:
        name = self._service_name(service)
        if not healthy:
            return False
        with self._lock:
            pending = self._read_pending()
            marker = pending.get(name)
            if not marker:
                return False
            current = self.service_status(name)
            current_invocation = str(current.get("invocation_id") or "")
            recorded_invocation = str(marker.get("invocation_id") or "")
            if not current_invocation or current_invocation == recorded_invocation:
                return False
            pending.pop(name, None)
            self._write_pending(pending)
            return True

    def public_status(self) -> dict[str, Any]:
        pending = self.pending()
        rows = []
        for service in MANAGED_SERVICES:
            row = self.service_status(service)
            marker = pending.get(service) or {}
            row["restart_required"] = bool(marker)
            row["restart_reasons"] = list(marker.get("reasons") or [])
            rows.append(row)
        return {
            "schema_version": 1,
            "services": rows,
            "restart_required": any(row["restart_required"] for row in rows),
        }

    def request_restart(self, service: Any, *, delay_seconds: float = 0.0) -> dict[str, Any]:
        name = self._service_name(service)
        status = self.service_status(name)
        if not status["restart_supported"]:
            raise ConsoleServiceError(
                "Home Suite cannot safely restart this service: "
                + str(status.get("unavailable_reason") or "restart is unavailable"),
                status=409,
            )
        pid = int(status["main_pid"])

        def signal_process() -> None:
            try:
                self._kill(pid, signal.SIGTERM)
            except OSError:
                pass

        delay = max(0.0, min(2.0, float(delay_seconds)))
        if delay:
            timer = threading.Timer(delay, signal_process)
            timer.daemon = True
            timer.start()
        else:
            try:
                self._kill(pid, signal.SIGTERM)
            except OSError as exc:
                raise ConsoleServiceError(
                    "The service process could not be signaled by the console user.",
                    status=409,
                ) from exc
        return {
            "service": name,
            "label": SERVICE_LABELS[name],
            "previous_pid": pid,
            "previous_invocation_id": status["invocation_id"],
            "restart_requested": True,
        }
