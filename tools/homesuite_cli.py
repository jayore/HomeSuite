#!/usr/bin/env python3
"""Canonical command-line entry point for operating one Home Suite node."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run_script(path: Path, args: list[str]) -> int:
    return subprocess.run([sys.executable, str(path), *args], cwd=ROOT, check=False).returncode


def _status() -> int:
    from tools.doctor import Doctor

    doctor = Doctor(live=True)
    return doctor.run()


def _logs(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="homesuite logs", description="Show bounded Home Suite diagnostics.")
    parser.add_argument("--events", action="store_true", help="show structured event metadata instead of the runtime log")
    parser.add_argument("--lines", type=int, default=100, help="number of trailing lines to show")
    parser.add_argument("--follow", action="store_true", help="continue streaming new lines")
    args = parser.parse_args(argv)
    lines = max(1, args.lines)
    path = ROOT / "logs" / "events.jsonl" if args.events else ROOT / "homesuite.log"
    if not path.exists():
        print(f"No log file found at {path}")
        return 1
    command = ["tail", "-n", str(lines)]
    if args.follow:
        command.append("-f")
    command.append(str(path))
    return subprocess.run(command, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv[:1] == ["logs"] and any(item in {"-h", "--help"} for item in raw_argv[1:]):
        return _logs(raw_argv[1:])

    parser = argparse.ArgumentParser(prog="homesuite", description="Operate and diagnose a Home Suite node.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command, help_text in (
        ("doctor", "check configuration and role readiness"),
        ("test", "run one command safely against live state by default"),
        ("repl", "open the safe interactive command shell"),
        ("calibrate-mic", "measure a microphone profile"),
        ("wakeword-lab", "capture or replay wakeword samples"),
        ("logs", "show runtime or structured event diagnostics"),
        ("support-bundle", "create a redacted diagnostic bundle"),
    ):
        command_parser = subparsers.add_parser(command, help=help_text)
        command_parser.add_argument("args", nargs=argparse.REMAINDER)

    subparsers.add_parser("status", help="run live node readiness checks")
    install_wakeword = subparsers.add_parser("install-wakeword", help="install optional OpenWakeWord dependencies")
    install_wakeword.add_argument("--upgrade", action="store_true", help="allow pip to upgrade already-installed wakeword packages")

    # Subcommands intentionally forward their own flags (for example
    # ``homesuite repl --live``) to the established tool implementations.
    args, unknown = parser.parse_known_args(raw_argv)
    forwarded = list(getattr(args, "args", []) or []) + list(unknown)
    scripts = {
        "doctor": ROOT / "tools" / "doctor.py",
        "test": ROOT / "tools" / "test_commands.py",
        "repl": ROOT / "command_repl.py",
        "calibrate-mic": ROOT / "tools" / "calibrate_mic.py",
        "wakeword-lab": ROOT / "tools" / "wakeword_lab.py",
        "support-bundle": ROOT / "tools" / "support_bundle.py",
    }
    if args.command in scripts:
        return _run_script(scripts[args.command], forwarded)
    if args.command == "logs":
        return _logs(forwarded)
    if args.command == "status":
        return _status()
    if args.command == "install-wakeword":
        command = [sys.executable, "-m", "pip", "install"]
        if args.upgrade:
            command.append("--upgrade")
        command.extend(["openwakeword", "onnxruntime"])
        return subprocess.run(command, cwd=ROOT, check=False).returncode
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
