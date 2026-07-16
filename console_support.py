"""Build a tightly allowlisted support bundle for console download."""

from __future__ import annotations

import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tools.support_bundle import write_bundle


ALLOWED_MEMBERS = frozenset({"README.txt", "doctor.json", "summary.json"})
MAX_BUNDLE_BYTES = 2 * 1024 * 1024


class ConsoleSupportError(RuntimeError):
    """Raised when a generated support artifact violates the console contract."""


@dataclass(frozen=True)
class ConsoleSupportBundle:
    filename: str
    content: bytes


def _validate_archive(path: Path) -> None:
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        names = {member.name for member in members}
        if names != ALLOWED_MEMBERS:
            raise ConsoleSupportError("The generated support bundle contained unexpected files.")
        if any(not member.isfile() or Path(member.name).name != member.name for member in members):
            raise ConsoleSupportError("The generated support bundle had an unsafe archive layout.")


def build_console_support_bundle(*, live: bool = False) -> ConsoleSupportBundle:
    """Return the existing redacted CLI bundle as bounded download bytes."""

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    filename = f"homesuite-support-{timestamp}.tar.gz"
    with tempfile.TemporaryDirectory(prefix="homesuite-console-support-") as temporary:
        output = Path(temporary) / filename
        write_bundle(output, live=bool(live))
        _validate_archive(output)
        if output.stat().st_size > MAX_BUNDLE_BYTES:
            raise ConsoleSupportError("The generated support bundle exceeded the console size limit.")
        content = output.read_bytes()
    return ConsoleSupportBundle(filename=filename, content=content)
