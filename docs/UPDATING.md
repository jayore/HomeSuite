# Updating a Home Suite Node

Use the updater on a node that was installed from Git. It fast-forwards the
current branch, refreshes declared Python dependencies, reinstalls command
shortcuts, and verifies required local configuration. It never writes
`private_config.py`, `deployment_config.py`, `local_prefs.py`, logs, or state.

## Normal Update

First make sure the checkout is clean. The updater intentionally refuses to
continue when tracked or untracked local changes exist.

```bash
cd ~/homesuite
git status --short
bash scripts/update.sh
homesuite doctor --live
```

Restart the active services only after the doctor and the relevant acceptance
checks pass. The management console and Telegram frontend are separate Python
processes, so code updates must reload them when they are running:

```bash
sudo systemctl restart homesuite.service
sudo systemctl try-restart homesuite-console.service
sudo systemctl try-restart piphone-telegram.service
homesuite status
```

For an already validated update, prefer having the updater perform that final
coordinated restart. It always restarts the runtime and reloads the console and
Telegram frontend when those optional processes are active:

```bash
bash scripts/update.sh --restart
```

## A Node With Local Work

Do not use forceful Git commands or delete files just to make an update pass.
Commit the work on a branch, stash it deliberately, or resolve it first. Then
rerun the updater. The refusal is there to protect local calibrations, fixes,
and in-progress development.

The updater stays on the currently checked-out branch. To intentionally update
another branch, check it out yourself first. `HOMESUITE_REMOTE` can select a
non-default Git remote when a deployment tracks a private mirror rather than
GitHub.

## Dependency and Service Notes

`scripts/update.sh` installs from `requirements.txt` by default. Use
`--skip-deps` only when dependencies have already been updated deliberately.
It runs non-live Doctor checks before restarting services, but it cannot prove
microphone placement, wakeword recognition, OAuth consent, or the behavior of
every configured device. Follow the node's section in
[Acceptance checks](ACCEPTANCE.md) after a meaningful update.

For a fresh install rather than an update, use [Install](INSTALL.md).
