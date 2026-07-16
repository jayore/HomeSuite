"""One-time browser-console claiming for fresh native installations."""

from __future__ import annotations

import ast
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from config_editor import atomic_write_config, rewrite_config_assignments


BOOTSTRAP_MARKER = "console_bootstrap_pending"
MIN_PASSPHRASE_LENGTH = 12


class ConsoleBootstrapError(RuntimeError):
    """Safe user-facing bootstrap failure with an HTTP status."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = int(status)


def _assigned_string(source: str, key: str) -> str:
    try:
        tree = ast.parse(source, filename="private_config.py")
    except SyntaxError as exc:
        raise ConsoleBootstrapError(
            "private_config.py has a syntax error and cannot be claimed safely.",
            status=409,
        ) from exc
    value: Any = ""
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            if node.targets[0].id != key:
                continue
            candidate = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id != key or node.value is None:
                continue
            candidate = node.value
        else:
            continue
        try:
            value = ast.literal_eval(candidate)
        except Exception:
            value = ""
    return str(value or "").strip()


class ConsoleBootstrap:
    """Claim a console only when its installer-created marker is present."""

    def __init__(self, *, root: Path) -> None:
        self.root = Path(root).resolve()
        self.marker_path = self.root / "state" / BOOTSTRAP_MARKER
        self.config_path = self.root / "private_config.py"
        self.backup_root = self.root / "backups" / "console"
        self._lock = threading.RLock()

    def pending(self) -> bool:
        if not self.marker_path.is_file() or not self.config_path.is_file():
            return False
        try:
            source = self.config_path.read_text(encoding="utf-8")
        except OSError:
            return False
        return not _assigned_string(source, "HOMESUITE_CONSOLE_KEY")

    def claim(self, passphrase: Any, confirmation: Any) -> dict[str, Any]:
        value = str(passphrase or "").strip()
        confirmed = str(confirmation or "").strip()
        if len(value) < MIN_PASSPHRASE_LENGTH:
            raise ConsoleBootstrapError(
                f"Use at least {MIN_PASSPHRASE_LENGTH} characters for the console passphrase."
            )
        if value != confirmed:
            raise ConsoleBootstrapError("The passphrases do not match.")

        with self._lock:
            if not self.pending():
                raise ConsoleBootstrapError(
                    "This console has already been claimed. Sign in with its saved passphrase.",
                    status=409,
                )
            source = self.config_path.read_text(encoding="utf-8")
            if _assigned_string(source, "HOMESUITE_CONSOLE_KEY"):
                raise ConsoleBootstrapError(
                    "This console has already been claimed. Sign in with its saved passphrase.",
                    status=409,
                )
            rewritten = rewrite_config_assignments(
                "private_config.py",
                source,
                updates={"HOMESUITE_CONSOLE_KEY": value},
                sort_dicts=False,
            )

            timestamp = time.strftime("%Y%m%d-%H%M%S")
            backup_dir = self.backup_root / f"{timestamp}-bootstrap"
            suffix = 1
            while backup_dir.exists():
                backup_dir = self.backup_root / f"{timestamp}-bootstrap-{suffix}"
                suffix += 1
            backup_dir.mkdir(parents=True, mode=0o700)
            os.chmod(backup_dir, 0o700)
            shutil.copy2(self.config_path, backup_dir / self.config_path.name)
            atomic_write_config(self.config_path, rewritten)
            self.marker_path.unlink(missing_ok=True)
            return {
                "claimed": True,
                "backup_dir": str(backup_dir),
                "console_key": value,
            }
