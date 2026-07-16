# Public Export

This repository is a sanitized public snapshot generated from the private HomeSuite development repo.

Source commit: `b1c1a46` (`b1c1a4686f248e825778f57d93aad3cb020919e9`)

Excluded from this public snapshot:

* `private_config.py`
* `local_prefs.py`
* `deployment_config.py`
* `backups/**`
* `logs/**`
* `state/**`
* `*.log`
* `*.log.*`
* `*.wav.transcript`
* `recording*.wav`
* `scheduled_jobs.json`
* `.scheduler.lock`
* `docs/AI_ARCHITECTURE_PLAN.md`
* `docs/AI_HANDOFF_LOG.md`
* `docs/AI_THREAD_GUIDE.md`
* `docs/AI_WORKFLOW_PREFERENCES.md`
* `docs/CLAUDE_CODE_WORKFLOW.md`
* `docs/DEV_AND_TESTING.md`
* `docs/FUTURE_ARCHITECTURE.md`
* `docs/handoffs/**`
* `assets/*.aiff`
* `assets/Funk.mp3`
* `assets/Mic_Switch_Off.wav`
* `assets/Radial-EncoreInfinitum.mp3`
* `assets/play.mp3`
* `assets/test.wav`
* `archive/**`
* `deploy/systemd/homesuite.service.current`
* `tools/export_public_repo.py`

Do not edit generated public snapshots by hand. Make changes in the private source repo, rerun `tools/export_public_repo.py`, then commit the refreshed public snapshot.
