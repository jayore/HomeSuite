from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from console_service_manager import (
    CONSOLE_SERVICE,
    RUNTIME_SERVICE,
    ConsoleServiceError,
    ConsoleServiceManager,
)


class _SystemdRunner:
    def __init__(self) -> None:
        self.invocations = {
            RUNTIME_SERVICE: "runtime-a",
            CONSOLE_SERVICE: "console-a",
        }
        self.pids = {
            RUNTIME_SERVICE: 1200,
            CONSOLE_SERVICE: 1300,
        }

    def __call__(self, command, **_kwargs):
        service = command[2]
        output = (
            "LoadState=loaded\n"
            "ActiveState=active\n"
            "SubState=running\n"
            f"MainPID={self.pids[service]}\n"
            "Restart=always\n"
            f"InvocationID={self.invocations[service]}\n"
        )
        return SimpleNamespace(returncode=0, stdout=output, stderr="")


class ConsoleServiceManagerTests(unittest.TestCase):
    def build_manager(self, root: Path, runner: _SystemdRunner, killed: list[tuple[int, int]]):
        return ConsoleServiceManager(
            root=root,
            runner=runner,
            kill=lambda pid, sig: killed.append((pid, sig)),
            stat=lambda _path: SimpleNamespace(st_uid=1000),
            effective_uid=lambda: 1000,
        )

    def test_status_allows_only_same_user_restart_always_units(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = self.build_manager(Path(temp), _SystemdRunner(), [])
            status = manager.public_status()

        self.assertFalse(status["restart_required"])
        self.assertEqual([row["service"] for row in status["services"]], [RUNTIME_SERVICE, CONSOLE_SERVICE])
        self.assertTrue(all(row["restart_supported"] for row in status["services"]))

    def test_pending_state_survives_manager_recreation_and_clears_after_new_healthy_invocation(self):
        runner = _SystemdRunner()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manager = self.build_manager(root, runner, [])
            manager.mark_required([RUNTIME_SERVICE], ["Audio configuration", "Audio configuration"])

            recreated = self.build_manager(root, runner, [])
            pending = recreated.public_status()["services"][0]
            self.assertTrue(pending["restart_required"])
            self.assertEqual(pending["restart_reasons"], ["Audio configuration"])
            self.assertFalse(recreated.reconcile(RUNTIME_SERVICE, healthy=True))

            runner.invocations[RUNTIME_SERVICE] = "runtime-b"
            self.assertFalse(recreated.reconcile(RUNTIME_SERVICE, healthy=False))
            self.assertTrue(recreated.reconcile(RUNTIME_SERVICE, healthy=True))
            self.assertFalse(recreated.public_status()["restart_required"])

    def test_restart_signals_exact_systemd_main_pid(self):
        runner = _SystemdRunner()
        killed: list[tuple[int, int]] = []
        with tempfile.TemporaryDirectory() as temp:
            manager = self.build_manager(Path(temp), runner, killed)
            result = manager.request_restart(RUNTIME_SERVICE)

        self.assertEqual(result["previous_pid"], 1200)
        self.assertEqual(killed[0][0], 1200)

    def test_unknown_service_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = self.build_manager(Path(temp), _SystemdRunner(), [])
            with self.assertRaisesRegex(ConsoleServiceError, "Only Home Suite"):
                manager.request_restart("ssh.service")

    def test_different_service_user_disables_restart(self):
        runner = _SystemdRunner()
        with tempfile.TemporaryDirectory() as temp:
            manager = ConsoleServiceManager(
                root=Path(temp),
                runner=runner,
                stat=lambda _path: SimpleNamespace(st_uid=2000),
                effective_uid=lambda: 1000,
            )
            with self.assertRaisesRegex(ConsoleServiceError, "different user"):
                manager.request_restart(RUNTIME_SERVICE)


if __name__ == "__main__":
    unittest.main()
