from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TimerEditParserTests(unittest.TestCase):
    def test_pause_and_resume_require_timer_language(self):
        import alarm_controls

        self.assertEqual(
            alarm_controls._parse_timer_edit_request("pause the pasta timer"),
            {"action": "pause", "target": "pasta", "seconds": None},
        )
        self.assertEqual(
            alarm_controls._parse_timer_edit_request("continue my timer"),
            {"action": "resume", "target": "", "seconds": None},
        )
        self.assertIsNone(alarm_controls._parse_timer_edit_request("pause"))

    def test_add_and_subtract_variants_parse_duration(self):
        import alarm_controls

        added = alarm_controls._parse_timer_edit_request(
            "add five minutes to my pasta timer"
        )
        removed = alarm_controls._parse_timer_edit_request(
            "take two minutes off the pasta timer"
        )

        self.assertEqual(added["action"], "add")
        self.assertEqual(added["target"], "pasta")
        self.assertEqual(added["seconds"], 300.0)
        self.assertEqual(removed["action"], "subtract")
        self.assertEqual(removed["target"], "pasta")
        self.assertEqual(removed["seconds"], 120.0)

    def test_set_remaining_time_variants_parse(self):
        import alarm_controls

        direct = alarm_controls._parse_timer_edit_request(
            "set the pasta timer to fifteen minutes"
        )
        explicit = alarm_controls._parse_timer_edit_request(
            "change the time left on my pasta timer to 20 minutes"
        )

        self.assertEqual(direct["action"], "set")
        self.assertEqual(direct["target"], "pasta")
        self.assertEqual(direct["seconds"], 900.0)
        self.assertEqual(explicit["action"], "set")
        self.assertEqual(explicit["target"], "pasta")
        self.assertEqual(explicit["seconds"], 1_200.0)

    def test_snooze_accepts_explicit_and_default_durations(self):
        import alarm_controls

        explicit = alarm_controls._parse_snooze_request(
            "snooze the pasta timer for five minutes"
        )
        with mock.patch.object(
            alarm_controls,
            "_prefs",
            side_effect=lambda name, default: 8 if name == "ALARM_DEFAULT_SNOOZE_MINUTES" else default,
        ):
            defaulted = alarm_controls._parse_snooze_request("snooze my alarm")

        self.assertEqual(explicit["kind"], "timer")
        self.assertEqual(explicit["target"], "pasta")
        self.assertEqual(explicit["seconds"], 300.0)
        self.assertEqual(defaulted["kind"], "alarm")
        self.assertEqual(defaulted["seconds"], 480.0)

    def test_referent_followup_accepts_acknowledgement_prefix(self):
        import alarm_controls

        parsed = alarm_controls._parse_scheduled_referent_followup(
            "Okay, add five minutes to it"
        )

        self.assertEqual(parsed["intent"], "timer_edit")
        self.assertEqual(parsed["action"], "add")
        self.assertEqual(parsed["seconds"], 300.0)


class SchedulerTimerMutationTests(unittest.TestCase):
    def setUp(self):
        import scheduler

        self.scheduler = scheduler
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "scheduled_jobs.json"
        self.db_patch = mock.patch.object(scheduler, "DB_PATH", str(self.db_path))
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.tempdir.cleanup()

    def test_pause_resume_and_reschedule_are_persisted(self):
        scheduler = self.scheduler
        job = scheduler.schedule_command("test command", 1_100.0)

        paused = scheduler.pause_job(job["id"], now_epoch=1_000.0)
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["remaining_seconds"], 100.0)

        shifted = scheduler.reschedule_job(
            job["id"],
            1_160.0,
            remaining_seconds=160.0,
        )
        self.assertEqual(shifted["status"], "paused")
        self.assertEqual(shifted["remaining_seconds"], 160.0)

        resumed = scheduler.resume_job(job["id"], 1_200.0)
        self.assertEqual(resumed["status"], "pending")
        self.assertEqual(resumed["run_at"], 1_200.0)
        self.assertNotIn("remaining_seconds", resumed)

        saved = scheduler.list_jobs()[0]
        self.assertEqual(saved["status"], "pending")
        self.assertEqual(saved["run_at"], 1_200.0)

    def test_non_pending_job_cannot_be_paused(self):
        scheduler = self.scheduler
        job = scheduler.schedule_command("test command", 1_100.0)
        rows = scheduler.list_jobs()
        rows[0]["status"] = "done"
        scheduler._save(rows)

        self.assertIsNone(scheduler.pause_job(job["id"], now_epoch=1_000.0))


class TimerEditLifecycleTests(unittest.TestCase):
    def setUp(self):
        import dialogue_state

        dialogue_state.reset_dialogue_state(all_scopes=True)

    def tearDown(self):
        import dialogue_state

        dialogue_state.reset_dialogue_state(all_scopes=True)

    @staticmethod
    def _timer(**updates):
        row = {
            "id": "timer-1",
            "scheduler_job_id": "job-1",
            "kind": "timer",
            "label": "pasta",
            "phrase": "in 10 minutes",
            "run_at": 1_600.0,
            "status": "pending",
            "output": {"mode": "local"},
        }
        row.update(updates)
        return row

    def test_pause_updates_scheduler_and_alarm_state(self):
        import alarm_controls
        import scheduler

        timer = self._timer()
        with (
            mock.patch.object(alarm_controls, "_active_alarms", return_value=[timer]),
            mock.patch.object(alarm_controls, "_should_persist_alarm", return_value=True),
            mock.patch.object(alarm_controls.time, "time", return_value=1_000.0),
            mock.patch.object(
                scheduler,
                "pause_job",
                return_value={"remaining_seconds": 600.0, "status": "paused"},
            ) as pause_job,
            mock.patch.object(alarm_controls, "_update_alarm") as update_alarm,
        ):
            response = alarm_controls.handle_alarm_controls(tl="pause the pasta timer")

        self.assertEqual(response, "Paused the pasta timer with 10 minutes remaining.")
        pause_job.assert_called_once_with("job-1", now_epoch=1_000.0)
        update_alarm.assert_called_once_with(
            "timer-1",
            status="paused",
            paused_at=1_000.0,
            remaining_seconds=600.0,
        )

    def test_new_timer_immediately_supports_add_time_to_it(self):
        import alarm_controls

        created = self._timer(
            id="abcd1234",
            scheduler_job_id="job-new",
            label="",
            phrase="in 5 minutes",
            run_at=1_300.0,
            created_at=1_000.0,
        )
        parsed = {
            "kind": "timer",
            "label": None,
            "run_at": 1_300.0,
            "phrase": "in 5 minutes",
            "output": {"mode": "local"},
            "action_command": None,
            "music_command": None,
        }
        with (
            mock.patch.object(alarm_controls, "_parse_create_alarm", return_value=parsed),
            mock.patch.object(alarm_controls, "_should_persist_alarm", return_value=False),
            mock.patch.object(alarm_controls.uuid, "uuid4") as uuid4,
            mock.patch.object(alarm_controls.time, "time", return_value=1_000.0),
        ):
            uuid4.return_value.hex = "abcd1234more"
            first = alarm_controls.handle_alarm_controls(tl="set a 5 minute timer")

        with (
            mock.patch.object(alarm_controls, "_load_alarms", return_value=[created]),
            mock.patch.object(alarm_controls, "_should_persist_alarm", return_value=False),
            mock.patch.object(alarm_controls.time, "time", return_value=1_000.0),
        ):
            second = alarm_controls.handle_alarm_controls(tl="add 1 minute to it")

        self.assertEqual(first, "5 minute timer set.")
        self.assertEqual(
            second,
            "Added 1 minute. The 5 minute timer now has 6 minutes remaining.",
        )

    def test_resume_preserves_paused_remaining_time(self):
        import alarm_controls
        import scheduler

        timer = self._timer(status="paused", remaining_seconds=90.0)
        with (
            mock.patch.object(alarm_controls, "_active_alarms", return_value=[timer]),
            mock.patch.object(alarm_controls, "_should_persist_alarm", return_value=True),
            mock.patch.object(alarm_controls.time, "time", return_value=1_000.0),
            mock.patch.object(
                scheduler,
                "resume_job",
                return_value={"run_at": 1_090.0, "status": "pending"},
            ) as resume_job,
            mock.patch.object(alarm_controls, "_update_alarm") as update_alarm,
        ):
            response = alarm_controls.handle_alarm_controls(tl="resume the pasta timer")

        self.assertEqual(response, "Resumed the pasta timer with 2 minutes remaining.")
        resume_job.assert_called_once_with("job-1", 1_090.0)
        self.assertEqual(update_alarm.call_args.args[0], "timer-1")
        self.assertEqual(update_alarm.call_args.kwargs["status"], "pending")
        self.assertEqual(update_alarm.call_args.kwargs["run_at"], 1_090.0)

    def test_add_time_reschedules_running_timer(self):
        import alarm_controls
        import scheduler

        timer = self._timer()
        with (
            mock.patch.object(alarm_controls, "_active_alarms", return_value=[timer]),
            mock.patch.object(alarm_controls, "_should_persist_alarm", return_value=True),
            mock.patch.object(alarm_controls.time, "time", return_value=1_000.0),
            mock.patch.object(
                scheduler,
                "reschedule_job",
                return_value={"run_at": 1_900.0, "status": "pending"},
            ) as reschedule_job,
            mock.patch.object(alarm_controls, "_update_alarm") as update_alarm,
        ):
            response = alarm_controls.handle_alarm_controls(
                tl="add five minutes to the pasta timer"
            )

        self.assertEqual(
            response,
            "Added 5 minutes. The pasta timer now has 15 minutes remaining.",
        )
        reschedule_job.assert_called_once_with(
            "job-1",
            1_900.0,
            remaining_seconds=None,
        )
        self.assertEqual(update_alarm.call_args.kwargs["run_at"], 1_900.0)

    def test_subtract_refuses_to_make_timer_immediately_due(self):
        import alarm_controls
        import scheduler

        timer = self._timer(run_at=1_060.0)
        with (
            mock.patch.object(alarm_controls, "_active_alarms", return_value=[timer]),
            mock.patch.object(alarm_controls, "_should_persist_alarm", return_value=True),
            mock.patch.object(alarm_controls.time, "time", return_value=1_000.0),
            mock.patch.object(scheduler, "reschedule_job") as reschedule_job,
        ):
            response = alarm_controls.handle_alarm_controls(
                tl="remove two minutes from the pasta timer"
            )

        self.assertEqual(
            response,
            "That adjustment would leave no time on the timer, so I left it unchanged.",
        )
        reschedule_job.assert_not_called()

    def test_set_remaining_time_reschedules_from_now(self):
        import alarm_controls
        import scheduler

        timer = self._timer()
        with (
            mock.patch.object(alarm_controls, "_active_alarms", return_value=[timer]),
            mock.patch.object(alarm_controls, "_should_persist_alarm", return_value=True),
            mock.patch.object(alarm_controls.time, "time", return_value=1_000.0),
            mock.patch.object(
                scheduler,
                "reschedule_job",
                return_value={"run_at": 1_900.0, "status": "pending"},
            ) as reschedule_job,
            mock.patch.object(alarm_controls, "_update_alarm") as update_alarm,
        ):
            response = alarm_controls.handle_alarm_controls(
                tl="set the pasta timer to fifteen minutes"
            )

        self.assertEqual(response, "The pasta timer now has 15 minutes remaining.")
        reschedule_job.assert_called_once_with(
            "job-1",
            1_900.0,
            remaining_seconds=None,
        )
        self.assertEqual(update_alarm.call_args.kwargs["run_at"], 1_900.0)

    def test_multiple_unnamed_targets_require_disambiguation(self):
        import alarm_controls

        rows = [
            self._timer(id="timer-1", label="", run_at=1_600.0),
            self._timer(id="timer-2", label="", run_at=1_900.0),
        ]
        with (
            mock.patch.object(alarm_controls, "_active_alarms", return_value=rows),
            mock.patch.object(alarm_controls.time, "time", return_value=1_000.0),
        ):
            response = alarm_controls.handle_alarm_controls(tl="pause the timer")

        self.assertIn("multiple timers", response.lower())

    def test_unknown_named_timer_does_not_select_the_only_timer(self):
        import alarm_controls
        import scheduler

        timer = self._timer(label="tea")
        with (
            mock.patch.object(alarm_controls, "_active_alarms", return_value=[timer]),
            mock.patch.object(scheduler, "reschedule_job") as reschedule_job,
        ):
            response = alarm_controls.handle_alarm_controls(
                tl="set the pasta timer to fifteen minutes"
            )

        self.assertEqual(response, "You don't have a matching timer set.")
        reschedule_job.assert_not_called()

    def test_paused_timer_list_names_its_state(self):
        import alarm_controls

        timer = self._timer(status="paused", remaining_seconds=300.0)
        with mock.patch.object(alarm_controls, "_active_alarms", return_value=[timer]):
            response = alarm_controls._list_alarms_response(kind="timer")

        self.assertIn("paused with 5 minutes remaining", response)

    def test_named_timer_query_selects_one_timer(self):
        import alarm_controls

        rows = [
            self._timer(id="timer-1", label="pasta", run_at=1_600.0),
            self._timer(id="timer-2", label="tea", run_at=1_300.0),
        ]
        with (
            mock.patch.object(alarm_controls, "_active_alarms", return_value=rows),
            mock.patch.object(alarm_controls.time, "time", return_value=1_000.0),
        ):
            response = alarm_controls.handle_alarm_controls(
                tl="how much time is left on my pasta timer"
            )

        self.assertEqual(response, "The pasta timer has 10 minutes remaining.")

    def test_generic_timer_list_query_is_not_treated_as_a_name(self):
        import alarm_controls

        with mock.patch.object(alarm_controls, "_active_alarms", return_value=[]):
            response = alarm_controls.handle_alarm_controls(tl="what timers are set")

        self.assertEqual(response, "You don't have any timers set.")

    def test_named_alarm_query_selects_one_alarm(self):
        import alarm_controls

        rows = [
            self._timer(kind="alarm", id="alarm-1", label="work", run_at=2_000.0),
            self._timer(kind="alarm", id="alarm-2", label="school", run_at=3_000.0),
        ]
        with (
            mock.patch.object(alarm_controls, "_active_alarms", return_value=rows),
            mock.patch.object(alarm_controls, "_format_due_phrase", return_value="tomorrow at 7 AM"),
        ):
            response = alarm_controls.handle_alarm_controls(
                tl="what time is my work alarm"
            )

        self.assertEqual(response, "The work alarm is set tomorrow at 7 AM.")

    def test_snooze_rejects_timer_that_has_not_fired(self):
        import alarm_controls
        import scheduler

        timer = self._timer()
        with (
            mock.patch.object(alarm_controls, "_recent_snoozable_alarms", return_value=[]),
            mock.patch.object(alarm_controls, "_active_alarms", return_value=[timer]),
            mock.patch.object(scheduler, "reschedule_job") as reschedule_job,
        ):
            response = alarm_controls.handle_alarm_controls(
                tl="snooze the pasta timer for five minutes"
            )

        self.assertEqual(
            response,
            "That timer hasn't gone off yet. You can add time to it or change "
            "the time remaining instead.",
        )
        reschedule_job.assert_not_called()

    def test_rejected_snooze_supports_add_time_followup_by_stable_id(self):
        import alarm_controls
        import scheduler

        timer = self._timer()
        with (
            mock.patch.object(alarm_controls, "_recent_snoozable_alarms", return_value=[]),
            mock.patch.object(alarm_controls, "_active_alarms", return_value=[timer]),
            mock.patch.object(alarm_controls, "_load_alarms", return_value=[timer]),
            mock.patch.object(alarm_controls, "_should_persist_alarm", return_value=True),
            mock.patch.object(alarm_controls.time, "time", return_value=1_000.0),
            mock.patch.object(
                scheduler,
                "reschedule_job",
                return_value={"run_at": 1_900.0, "status": "pending"},
            ) as reschedule_job,
            mock.patch.object(alarm_controls, "_update_alarm") as update_alarm,
        ):
            first = alarm_controls.handle_alarm_controls(
                tl="snooze the pasta timer for five minutes"
            )
            second = alarm_controls.handle_alarm_controls(
                tl="okay, add five minutes to it"
            )

        self.assertIn("hasn't gone off yet", first)
        self.assertEqual(
            second,
            "Added 5 minutes. The pasta timer now has 15 minutes remaining.",
        )
        reschedule_job.assert_called_once_with(
            "job-1",
            1_900.0,
            remaining_seconds=None,
        )
        self.assertEqual(update_alarm.call_args.kwargs["run_at"], 1_900.0)

    def test_recent_timer_supports_implicit_remaining_time_query(self):
        import alarm_controls

        timer = self._timer()
        alarm_controls._remember_scheduled_referent(timer, source="test")
        with (
            mock.patch.object(alarm_controls, "_load_alarms", return_value=[timer]),
            mock.patch.object(alarm_controls.time, "time", return_value=1_000.0),
        ):
            response = alarm_controls.handle_alarm_controls(
                tl="how much time is left"
            )

        self.assertEqual(response, "The pasta timer has 10 minutes remaining.")

    def test_specific_query_then_cancel_it_uses_exact_alarm_id(self):
        import alarm_controls

        pasta = self._timer(id="timer-pasta", label="pasta")
        tea = self._timer(id="timer-tea", label="tea", run_at=1_900.0)
        rows = [pasta, tea]
        with (
            mock.patch.object(alarm_controls, "_active_alarms", return_value=rows),
            mock.patch.object(alarm_controls, "_load_alarms", return_value=rows),
            mock.patch.object(alarm_controls.time, "time", return_value=1_000.0),
            mock.patch.object(alarm_controls, "_cancel_alarm_row", return_value=True) as cancel,
        ):
            query = alarm_controls.handle_alarm_controls(
                tl="how much time is left on my pasta timer"
            )
            canceled = alarm_controls.handle_alarm_controls(tl="cancel it")

        self.assertEqual(query, "The pasta timer has 10 minutes remaining.")
        self.assertEqual(canceled, "Canceled the pasta timer.")
        self.assertEqual(cancel.call_args.args[0]["id"], "timer-pasta")

    def test_snooze_rejects_alarm_that_has_not_fired(self):
        import alarm_controls

        alarm = self._timer(kind="alarm", label="work")
        with (
            mock.patch.object(alarm_controls, "_recent_snoozable_alarms", return_value=[]),
            mock.patch.object(alarm_controls, "_active_alarms", return_value=[alarm]),
        ):
            response = alarm_controls.handle_alarm_controls(
                tl="snooze the work alarm for five minutes"
            )

        self.assertEqual(
            response,
            "That alarm hasn't gone off yet, so there's nothing to snooze.",
        )

    def test_snooze_rejects_attached_actions(self):
        import alarm_controls

        timer = self._timer(
            status="fired",
            completed_at=990.0,
            action_command="turn off all lights",
        )
        with (
            mock.patch.object(alarm_controls, "_recent_snoozable_alarms", return_value=[timer]),
            mock.patch.object(alarm_controls, "_active_alarms", return_value=[]),
        ):
            response = alarm_controls.handle_alarm_controls(
                tl="snooze the pasta timer for five minutes"
            )

        self.assertIn("can't safely snooze", response)

    def test_snooze_reschedules_a_recently_completed_timer(self):
        import alarm_controls

        timer = self._timer(status="fired", completed_at=990.0)
        with (
            mock.patch.object(alarm_controls, "_recent_snoozable_alarms", return_value=[timer]),
            mock.patch.object(alarm_controls, "_active_alarms", return_value=[]),
            mock.patch.object(alarm_controls, "_should_persist_alarm", return_value=True),
            mock.patch.object(alarm_controls.time, "time", return_value=1_000.0),
            mock.patch.object(
                alarm_controls,
                "_schedule_alarm_fire",
                return_value={"id": "job-2", "run_at": 1_300.0},
            ) as schedule,
            mock.patch.object(alarm_controls, "_update_alarm") as update_alarm,
        ):
            response = alarm_controls.handle_alarm_controls(
                tl="snooze the pasta timer for five minutes"
            )

        self.assertEqual(response, "Snoozed the pasta timer for 5 minutes.")
        schedule.assert_called_once()
        self.assertEqual(update_alarm.call_args.kwargs["scheduler_job_id"], "job-2")
        self.assertEqual(update_alarm.call_args.kwargs["status"], "pending")


if __name__ == "__main__":
    unittest.main()
