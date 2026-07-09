# Public Export

This repository is a sanitized public snapshot generated from the private HomeSuite development repo.

Source commit: `2a4540d` (`2a4540d4d4a699ed2cc08a34f733c11a8c44416c`)

Excluded from this public snapshot:

* `private_config.py`
* `local_prefs.py`
* `backups/**`
* `logs/**`
* `state/**`
* `*.log`
* `*.log.*`
* `*.wav.transcript`
* `recording*.wav`
* `scheduled_jobs.json`
* `.scheduler.lock`
* `docs/AI_HANDOFF_LOG.md`
* `docs/AI_THREAD_GUIDE.md`
* `docs/AI_WORKFLOW_PREFERENCES.md`
* `docs/CLAUDE_CODE_WORKFLOW.md`
* `docs/DEV_AND_TESTING.md`
* `docs/handoffs/**`
* `archive/**`
* `deploy/systemd/homesuite.service.current`
* `tools/export_public_repo.py`

Do not edit generated public snapshots by hand. Make changes in the private source repo, rerun `tools/export_public_repo.py`, then commit the refreshed public snapshot.
