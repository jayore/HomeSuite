from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from console_wakewords import ConsoleWakewordError, ConsoleWakewordManager


def app_config(**overrides):
    values = {
        "WAKEWORD_ENABLED": True,
        "WAKEWORD_ENGINE": "openwakeword",
        "WAKEWORD_MODEL": "",
        "WAKEWORD_MODEL_PATHS": [],
        "WAKEWORD_THRESHOLD": 0.5,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ConsoleWakewordManagerTests(unittest.TestCase):
    def manager(self, root: Path, config, *, probe=None):
        editor = mock.Mock()
        editor.app_config = config
        editor.preview.return_value = {
            "changes": [],
            "change_count": 0,
            "revisions": {"local_prefs.py": "revision"},
            "restart_services": ["homesuite.service"],
        }
        editor.apply.return_value = {
            "applied": True,
            "changes": [],
            "change_count": 0,
            "written_files": ["local_prefs.py"],
            "restart_services": ["homesuite.service"],
            "backup_dir": str(root / "backup"),
        }
        manager = ConsoleWakewordManager(
            root=root,
            editor=editor,
            app_config=config,
            model_probe=probe or (lambda path: {"validated": True, "label": path.stem}),
            extra_model_dirs=[],
        )
        return manager, editor

    def test_discovers_configured_and_managed_models(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            managed = root / "wake_models"
            managed.mkdir()
            (managed / "hey_spock.onnx").write_bytes(b"spock")
            (managed / "hey_spock.json").write_text(
                '{"managed_by": "homesuite-console"}\n',
                encoding="utf-8",
            )
            external = root / "existing"
            external.mkdir()
            hal = external / "hal_v2.onnx"
            hal.write_bytes(b"hal")
            config = app_config(WAKEWORD_MODEL_PATHS=[str(hal)])
            manager, _editor = self.manager(root, config)

            state = manager.public_state()

            self.assertTrue(state["multiple_allowed"])
            self.assertEqual(state["selected_count"], 1)
            by_filename = {row["filename"]: row for row in state["models"]}
            self.assertTrue(by_filename["hal_v2.onnx"]["selected"])
            self.assertEqual(by_filename["hal_v2.onnx"]["source"], "Local file")
            self.assertFalse(by_filename["hey_spock.onnx"]["selected"])
            self.assertTrue(by_filename["hey_spock.onnx"]["managed"])

    def test_builtin_selection_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manager, _editor = self.manager(
                root,
                app_config(WAKEWORD_MODEL="hey_mycroft", WAKEWORD_MODEL_PATHS=[]),
            )

            state = manager.public_state()

            self.assertEqual(state["selected_count"], 1)
            self.assertEqual(state["models"][0]["label"], "hey_mycroft")
            self.assertEqual(state["models"][0]["source"], "OpenWakeWord built-in")

    def test_preview_maps_multiple_selected_models_to_existing_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model_dir = root / "wake_models"
            model_dir.mkdir()
            first = model_dir / "hal.onnx"
            second = model_dir / "computer.onnx"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            manager, editor = self.manager(root, app_config(WAKEWORD_ENABLED=False))
            state = manager.public_state()
            ids = [row["id"] for row in state["models"]]

            result = manager.preview(active_ids=ids, enabled=True)

            self.assertEqual(result["selection"]["active_count"], 2)
            changes = editor.preview.call_args.args[0]
            by_key = {change["key"]: change["value"] for change in changes}
            self.assertTrue(by_key["WAKEWORD_ENABLED"])
            self.assertEqual(
                set(by_key["WAKEWORD_MODEL_PATHS"]),
                {str(first.resolve()), str(second.resolve())},
            )
            self.assertEqual(by_key["WAKEWORD_MODEL"], "")

    def test_enabled_selection_requires_at_least_one_model(self):
        with tempfile.TemporaryDirectory() as temp:
            manager, _editor = self.manager(Path(temp), app_config(WAKEWORD_ENABLED=False))
            with self.assertRaisesRegex(ConsoleWakewordError, "Select at least one"):
                manager.preview(active_ids=[], enabled=True)

    def test_missing_configured_model_can_be_seen_but_not_activated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            missing = root / "missing.onnx"
            manager, _editor = self.manager(
                root,
                app_config(WAKEWORD_MODEL_PATHS=[str(missing)]),
            )
            state = manager.public_state()
            self.assertFalse(state["models"][0]["exists"])
            with self.assertRaisesRegex(ConsoleWakewordError, "missing from this device"):
                manager.preview(active_ids=[state["models"][0]["id"]], enabled=True)

    def test_upload_is_validated_deduplicated_and_removable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            probe = mock.Mock(return_value={"validated": True, "label": "hal_v3", "warning": ""})
            manager, _editor = self.manager(root, app_config(WAKEWORD_ENABLED=False), probe=probe)

            temporary = manager.create_upload_path()
            temporary.write_bytes(b"fake onnx bytes")
            first = manager.install_uploaded_file(temporary, "HAL v3.onnx")

            self.assertTrue(first["added"])
            self.assertEqual(first["model"]["label"], "hal-v3")
            self.assertEqual(Path(first["model"]["path"]).name, "hal-v3.onnx")
            self.assertTrue(Path(first["model"]["path"]).is_file())
            self.assertTrue(Path(first["model"]["path"]).with_suffix(".json").is_file())

            duplicate = manager.create_upload_path()
            duplicate.write_bytes(b"fake onnx bytes")
            second = manager.install_uploaded_file(duplicate, "HAL v3.onnx")
            self.assertFalse(second["added"])

            collision = manager.create_upload_path()
            collision.write_bytes(b"different onnx bytes")
            third = manager.install_uploaded_file(collision, "HAL v3.onnx")
            self.assertTrue(third["added"])
            self.assertRegex(Path(third["model"]["path"]).name, r"^hal-v3-[0-9a-f]{10}\.onnx$")

            removed = manager.remove(first["model"]["id"])
            self.assertTrue(removed["removed"])
            self.assertFalse(Path(first["model"]["path"]).exists())

    def test_active_uploaded_model_must_be_deactivated_before_removal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model_dir = root / "wake_models"
            model_dir.mkdir()
            model = model_dir / "hal.onnx"
            model.write_bytes(b"model")
            manager, _editor = self.manager(
                root,
                app_config(WAKEWORD_MODEL_PATHS=[str(model)]),
            )
            model_id = manager.public_state()["models"][0]["id"]
            with self.assertRaisesRegex(ConsoleWakewordError, "Deactivate"):
                manager.remove(model_id)

    def test_duplicate_upload_does_not_claim_preexisting_local_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model_dir = root / "wake_models"
            model_dir.mkdir()
            existing = model_dir / "hal.onnx"
            existing.write_bytes(b"existing model")
            manager, _editor = self.manager(root, app_config(WAKEWORD_ENABLED=False))

            temporary = manager.create_upload_path()
            temporary.write_bytes(b"existing model")
            result = manager.install_uploaded_file(temporary, "hal.onnx")

            self.assertFalse(result["added"])
            self.assertFalse(result["model"]["managed"])
            self.assertFalse(result["model"]["removable"])
            self.assertFalse(existing.with_suffix(".json").exists())

    def test_upload_rejects_non_onnx_filename(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manager, _editor = self.manager(root, app_config())
            temporary = manager.create_upload_path()
            temporary.write_bytes(b"data")
            try:
                with self.assertRaisesRegex(ConsoleWakewordError, r"\.onnx"):
                    manager.install_uploaded_file(temporary, "model.tflite")
            finally:
                temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
