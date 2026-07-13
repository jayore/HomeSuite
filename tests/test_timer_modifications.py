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

    def test_paused_timer_list_names_its_state(self):
        import alarm_controls

        timer = self._timer(status="paused", remaining_seconds=300.0)
        with mock.patch.object(alarm_controls, "_active_alarms", return_value=[timer]):
            response = alarm_controls._list_alarms_response(kind="timer")

        self.assertIn("paused with 5 minutes remaining", response)


if __name__ == "__main__":
    unittest.main()
