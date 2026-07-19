from __future__ import annotations

import json
import os
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, ExitStack
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _Response:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self._body


class SatelliteTransportTests(unittest.TestCase):
    def test_bare_server_url_is_normalized_to_command_endpoint(self):
        from satellite_runtime import normalize_command_url

        self.assertEqual(
            normalize_command_url("http://piphone.local:8765/"),
            "http://piphone.local:8765/command",
        )
        self.assertEqual(
            normalize_command_url("http://piphone.local:8765/command"),
            "http://piphone.local:8765/command",
        )

    def test_forward_command_preserves_source_room_and_auth(self):
        from satellite_runtime import forward_command

        response = {
            "ok": True,
            "handled": True,
            "action_occurred": False,
            "response": "It's 4:12 PM.",
            "source": "device_text",
            "request_id": "req-1",
            "context": {
                "source_id": "piphone1",
                "source_type": "satellite",
                "source_room": "living_room",
            },
        }
        with mock.patch(
            "satellite_runtime.urllib.request.urlopen",
            return_value=_Response(response),
        ) as urlopen:
            result = forward_command(
                "what time is it",
                brain_url="http://piphone.local:8765",
                api_key="shared-secret",
                source_id="piphone1",
                source_room="living_room",
                trigger="wakeword",
                request_id="req-1",
                timing={
                    "schema_version": 1,
                    "utterance_id": "utterance-1",
                    "speech": {"started_at_ms": 1000, "ended_at_ms": 2000},
                },
                interaction_id="interaction-1",
                winner_token="winner-token",
            )

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "http://piphone.local:8765/command")
        self.assertEqual(request.get_header("X-api-key"), "shared-secret")
        self.assertEqual(payload["source_id"], "piphone1")
        self.assertEqual(payload["source_room"], "living_room")
        self.assertEqual(payload["source_type"], "satellite")
        self.assertEqual(payload["origin"], "satellite_wakeword")
        self.assertEqual(payload["timing"]["utterance_id"], "utterance-1")
        self.assertEqual(payload["interaction_id"], "interaction-1")
        self.assertEqual(payload["winner_token"], "winner-token")
        self.assertIn("satellite_sent_at_ms", payload["timing"])
        self.assertEqual(result.response_text, "It's 4:12 PM.")
        self.assertTrue(result.handled)
        self.assertFalse(result.cancelled)

    def test_missing_key_fails_before_network_io(self):
        from satellite_runtime import SatelliteRuntimeError, forward_command

        with (
            mock.patch("satellite_runtime.urllib.request.urlopen") as urlopen,
            self.assertRaisesRegex(SatelliteRuntimeError, "API key is empty"),
        ):
            forward_command(
                "turn off the light",
                brain_url="http://piphone.local:8765",
                api_key="",
                source_id="piphone1",
                source_room="living_room",
                trigger="wakeword",
            )
        urlopen.assert_not_called()


class SatelliteVoiceRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch.dict(os.environ, {"PIPHONE_NO_RUNTIME_INIT": "1"}):
            import main

        cls.main = main

    @contextmanager
    def _base_environment(self, transcript: str):
        main = self.main
        patchers = (
            mock.patch.object(main, "touch_session"),
            mock.patch.object(main, "refresh_runnable_cache"),
            mock.patch.object(main, "_perf"),
            mock.patch.object(main, "_trace_audio_event"),
            mock.patch.object(main, "transcribe_audio", return_value=transcript),
            mock.patch.object(main, "_strip_wakeword_prefix", side_effect=lambda text: text),
            mock.patch.object(main, "_satellite_mode_enabled", return_value=True),
            mock.patch.object(main, "_satellite_source_id", return_value="piphone1"),
            mock.patch.object(main, "_satellite_source_room", return_value="living_room"),
            mock.patch.object(main, "log_command_event"),
        )
        with ExitStack() as stack:
            for patcher in patchers:
                stack.enter_context(patcher)
            yield

    def test_handled_command_uses_brain_and_plays_response_locally(self):
        from satellite_runtime import SatelliteCommandResult

        main = self.main
        result = SatelliteCommandResult(
            handled=True,
            action_occurred=False,
            response_text="It's 4:12 PM.",
            source="device_text",
            request_id="req-1",
        )
        with (
            self._base_environment("what time is it"),
            mock.patch.object(main, "_forward_voice_command_to_brain", return_value=result) as forward,
            mock.patch.object(main, "process_device_commands") as local_route,
            mock.patch.object(
                main.interaction_flow,
                "route_unhandled_utterance",
            ) as local_semantic_route,
            mock.patch.object(main, "_speak_text_for_trigger", return_value=False) as speak,
            mock.patch.object(main, "play_error_sound") as error_tone,
            mock.patch.object(main, "play_sound") as success_tone,
        ):
            main.process_audio("ignored.wav", trigger="wakeword")

        forward.assert_called_once_with(
            "what time is it",
            trigger="wakeword",
            timing=None,
            wakeword_decision=None,
        )
        local_route.assert_not_called()
        local_semantic_route.assert_not_called()
        speak.assert_called_once_with("It's 4:12 PM.", "wakeword")
        error_tone.assert_not_called()
        success_tone.assert_called_once_with("finish", 1.0, blocking=False)

    def test_cancel_is_forwarded_to_clear_brain_state_and_stays_silent(self):
        from satellite_runtime import SatelliteCommandResult

        main = self.main
        result = SatelliteCommandResult(
            handled=True,
            action_occurred=False,
            response_text="",
            source="cancelled",
            request_id="req-2",
        )
        with (
            self._base_environment("never mind"),
            mock.patch.object(main, "_forward_voice_command_to_brain", return_value=result) as forward,
            mock.patch.object(main, "process_device_commands") as local_route,
            mock.patch.object(main, "_speak_text_for_trigger") as speak,
            mock.patch.object(main, "play_error_sound") as error_tone,
            mock.patch.object(main, "play_sound") as success_tone,
        ):
            main.process_audio("ignored.wav", trigger="wakeword")

        forward.assert_called_once_with(
            "never mind",
            trigger="wakeword",
            timing=None,
            wakeword_decision=None,
        )
        local_route.assert_not_called()
        speak.assert_not_called()
        error_tone.assert_not_called()
        success_tone.assert_not_called()

    def test_silent_action_confirmation_keeps_existing_tone_only_voice_policy(self):
        from satellite_runtime import SatelliteCommandResult

        main = self.main
        result = SatelliteCommandResult(
            handled=True,
            action_occurred=True,
            response_text="Turned off the stair light.",
            source="device_confirm",
            request_id="req-3",
        )
        with (
            self._base_environment("turn off the stair light"),
            mock.patch.object(main, "_forward_voice_command_to_brain", return_value=result),
            mock.patch.object(main, "_pref_bool", side_effect=lambda key, default=False: default),
            mock.patch.object(main, "_speak_text_for_trigger") as speak,
            mock.patch.object(main, "play_error_sound") as error_tone,
            mock.patch.object(main, "play_sound") as success_tone,
        ):
            main.process_audio("ignored.wav", trigger="wakeword")

        speak.assert_not_called()
        error_tone.assert_not_called()
        success_tone.assert_called_once_with("finish", 1.0, blocking=False)

    def test_brain_failure_does_not_fall_back_to_local_execution(self):
        from satellite_runtime import SatelliteRuntimeError

        main = self.main
        with (
            self._base_environment("turn off the stair light"),
            mock.patch.object(
                main,
                "_forward_voice_command_to_brain",
                side_effect=SatelliteRuntimeError("offline"),
            ),
            mock.patch.object(main, "process_device_commands") as local_route,
            mock.patch.object(
                main.interaction_flow,
                "route_unhandled_utterance",
            ) as local_semantic_route,
            mock.patch.object(main, "_speak_text_for_trigger") as speak,
            mock.patch.object(main, "play_error_sound") as error_tone,
            mock.patch.object(main, "play_sound") as success_tone,
        ):
            main.process_audio("ignored.wav", trigger="wakeword")

        local_route.assert_not_called()
        local_semantic_route.assert_not_called()
        speak.assert_not_called()
        error_tone.assert_called_once_with()
        success_tone.assert_not_called()

    def test_arbitration_suppression_is_silent_and_does_not_route_locally(self):
        from satellite_runtime import SatelliteCommandResult

        main = self.main
        result = SatelliteCommandResult(
            handled=False,
            action_occurred=False,
            response_text="",
            source="arbitration_suppressed",
            request_id="req-suppressed",
            disposition="suppressed",
        )
        with (
            self._base_environment("turn off the stair light"),
            mock.patch.object(main, "_forward_voice_command_to_brain", return_value=result),
            mock.patch.object(main, "process_device_commands") as local_route,
            mock.patch.object(main, "_speak_text_for_trigger") as speak,
            mock.patch.object(main, "play_error_sound") as error_tone,
            mock.patch.object(main, "play_sound") as success_tone,
        ):
            main.process_audio("ignored.wav", trigger="wakeword")

        local_route.assert_not_called()
        speak.assert_not_called()
        error_tone.assert_not_called()
        success_tone.assert_not_called()

    def test_losing_wake_candidate_never_enters_capture_or_barge_in(self):
        from satellite_coordination import WakewordDecision

        main = self.main
        decision = WakewordDecision(
            disposition="suppressed",
            candidate_id="candidate-loser",
            interaction_id="interaction-1",
            winner_source_id="kitchen",
            reason="better_candidate",
            eligible_wakeword_nodes=2,
        )
        with (
            mock.patch.object(main, "_satellite_mode_enabled", return_value=True),
            mock.patch.object(main, "_satellite_source_id", return_value="hall"),
            mock.patch.object(main, "_request_wakeword_decision", return_value=decision),
            mock.patch.object(main, "_process_wakeword_stream_interaction") as process,
            mock.patch.object(main, "_wakeword_rearm_sec", return_value=0.0),
            mock.patch.object(main, "_is_sfx_playing", return_value=False),
            mock.patch.object(main, "_trace_audio_event"),
            mock.patch.object(main, "stop_speaking_now") as stop_speaking,
        ):
            main._handle_wakeword_detected(
                frame_reader=lambda: None,
                sample_rate=16000,
                frame_samples=160,
                wakeword_label="hal_v2",
                wakeword_score=0.90,
            )

        process.assert_not_called()
        stop_speaking.assert_not_called()
        self.assertFalse(main._WAKEWORD_DETECTION_IN_PROGRESS)

    def test_single_node_candidate_skips_unneeded_audio_quality_work(self):
        from satellite_coordination import WakewordDecision

        main = self.main
        client = mock.Mock()
        client.status.return_value = {
            "connected": True,
            "eligible_wakeword_nodes": 1,
        }
        client.request_wakeword_decision.return_value = WakewordDecision(
            disposition="granted",
            candidate_id="candidate-one",
            interaction_id="interaction-one",
            winner_token="winner-token",
            winner_source_id="piphone1",
            eligible_wakeword_nodes=1,
        )
        timing = {"utterance_id": "candidate-one"}
        with (
            mock.patch.object(main, "_satellite_mode_enabled", return_value=True),
            mock.patch.object(main, "_wakeword_arbitration_enabled", return_value=True),
            mock.patch.object(main, "_satellite_source_room", return_value="living_room"),
            mock.patch.object(main, "_wakeword_detection_threshold", return_value=0.75),
            mock.patch.object(main, "_SATELLITE_COORDINATION_CLIENT", client),
            mock.patch.object(main, "measure_wake_audio_quality") as measure_quality,
        ):
            decision = main._request_wakeword_decision(
                timing,
                {
                    "wakeword_label": "hal_v2",
                    "wakeword_score": 0.90,
                    "pre_trigger_frames": [b"unused"],
                    "pre_trigger_sample_rate": 16000,
                },
            )

        self.assertTrue(decision.granted)
        measure_quality.assert_not_called()
        candidate_payload = client.request_wakeword_decision.call_args.args[0]
        self.assertEqual(candidate_payload["audio_quality"], {})

    def test_granted_wake_candidate_enters_existing_capture_path(self):
        from satellite_coordination import WakewordDecision

        main = self.main
        decision = WakewordDecision(
            disposition="granted",
            candidate_id="candidate-winner",
            interaction_id="interaction-1",
            winner_token="winner-token",
            winner_source_id="piphone1",
            reason="winner",
            eligible_wakeword_nodes=1,
            election_hold_ms=0,
        )
        with (
            mock.patch.object(main, "_satellite_mode_enabled", return_value=True),
            mock.patch.object(main, "_satellite_source_id", return_value="piphone1"),
            mock.patch.object(main, "_request_wakeword_decision", return_value=decision),
            mock.patch.object(main, "_process_wakeword_stream_interaction", return_value=True) as process,
            mock.patch.object(main, "_wakeword_rearm_sec", return_value=0.0),
            mock.patch.object(main, "_is_sfx_playing", return_value=False),
            mock.patch.object(main, "_trace_audio_event"),
        ):
            frame_reader = lambda: None
            main._handle_wakeword_detected(
                frame_reader=frame_reader,
                sample_rate=16000,
                frame_samples=160,
                wakeword_label="hal_v2",
                wakeword_score=0.90,
            )

        self.assertIs(process.call_args.kwargs["wakeword_decision"], decision)
        self.assertIs(process.call_args.kwargs["frame_reader"], frame_reader)
        self.assertFalse(main._WAKEWORD_DETECTION_IN_PROGRESS)

    def test_ptt_satellite_command_bypasses_wakeword_arbitration(self):
        from satellite_runtime import SatelliteCommandResult

        main = self.main
        result = SatelliteCommandResult(
            handled=True,
            action_occurred=True,
            response_text="",
            source="device_confirm",
            request_id="req-ptt",
        )
        with (
            self._base_environment("turn off the stair light"),
            mock.patch.object(main, "_forward_voice_command_to_brain", return_value=result) as forward,
            mock.patch.object(main, "_ptt_enabled", return_value=False),
            mock.patch.object(main, "play_error_sound") as error_tone,
            mock.patch.object(main, "play_sound") as success_tone,
        ):
            main.process_audio("ignored.wav", trigger="ptt")

        forward.assert_called_once_with(
            "turn off the stair light",
            trigger="ptt",
            timing=None,
            wakeword_decision=None,
        )
        error_tone.assert_not_called()
        success_tone.assert_called_once_with("finish", 1.0, blocking=False)


class SatelliteRoomContextTests(unittest.TestCase):
    def test_satellite_with_source_room_is_room_local(self):
        from request_context import (
            build_request_context,
            replace_current_request_context,
            request_has_room_local_context,
            set_current_request_context,
        )

        context = build_request_context(
            source_id="piphone1",
            source_type="satellite",
            source_room="living_room",
        )
        previous = replace_current_request_context(context)
        try:
            self.assertTrue(request_has_room_local_context())
        finally:
            set_current_request_context(previous)

    def test_request_context_is_isolated_between_local_and_http_threads(self):
        from request_context import (
            build_request_context,
            get_current_request_context,
            replace_current_request_context,
            set_current_request_context,
        )

        local_context = build_request_context(source_id="default_piphone", source_room="office")
        remote_context = build_request_context(
            source_id="piphone1",
            source_type="satellite",
            source_room="living_room",
        )
        previous = replace_current_request_context(local_context)

        def remote_turn():
            self.assertIsNone(get_current_request_context())
            worker_previous = replace_current_request_context(remote_context)
            try:
                return get_current_request_context()
            finally:
                set_current_request_context(worker_previous)

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                observed_remote = executor.submit(remote_turn).result(timeout=2)
            self.assertEqual(observed_remote, remote_context)
            self.assertEqual(get_current_request_context(), local_context)
        finally:
            set_current_request_context(previous)


if __name__ == "__main__":
    unittest.main()
