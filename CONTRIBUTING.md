# Contributing to Home Suite

Home Suite is a public-alpha project and a daily driver in its original
deployment. Changes should preserve predictable command execution and keep PTT,
wakeword, text, and companion-client behavior isolated where their hardware or
timing policies differ.

Open an issue before a large feature or routing change so scope and integration
boundaries can be agreed first. Small bug fixes, tests, and documentation
corrections can go directly to a pull request.

## Development Setup

Use the native installer or create the project virtual environment described in
[Install](docs/INSTALL.md). Never commit real `private_config.py`,
`deployment_config.py`, `local_prefs.py`, OAuth files, tokens, recordings, or
generated state.

For test-only imports, runtime hardware initialization must remain disabled.
The existing tests set the needed environment around modules that import
`main.py`.

## Before Submitting

Run the complete unit suite:

```bash
cd ~/homesuite
.venv/bin/python -m unittest discover -s tests -v
```

Run syntax and patch checks:

```bash
.venv/bin/python -m py_compile main.py command_dispatch.py app_config.py
git diff --check
```

For router changes, also run:

```bash
.venv/bin/python tools/router_smoke_test.py
pptest "the exact phrase you changed"
```

Use `pplive` only for a small number of deliberate actions against your own
Home Assistant deployment. Audio changes should also run the focused wakeword
tests and be exercised on the affected PTT or wakeword hardware.

## Design Expectations

* Prefer existing deterministic handlers for known home actions.
* Do not let model output invent entity IDs or call arbitrary services.
* Keep shared topology in deployment configuration and one-device hardware
  settings in local preferences.
* Add configuration only when behavior genuinely needs to vary.
* Preserve graceful behavior when optional integrations are blank.
* Add tests proportional to routing, shared-state, or hardware blast radius.
* Update the relevant public guide when behavior or configuration changes.

## Pull Requests

Describe:

* the user-visible problem and resulting behavior
* which command surfaces or hardware paths are affected
* configuration or migration implications
* tests run and any hardware behavior not tested

The private development repository is the source of truth for the original
deployment. GitHub receives a sanitized public snapshot, so public history must
never contain local credentials or deployment-only files.
