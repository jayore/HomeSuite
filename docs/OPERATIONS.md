# Operations and Privacy

Home Suite is designed to run continuously on a trusted home network. This
guide covers the small operational habits that keep a long-lived node readable,
bounded, and supportable.

## Status and Logs

Use the canonical command-line interface:

```bash
homesuite status
homesuite doctor --live
homesuite logs --lines 100
```

The service also appears in `journalctl`:

```bash
sudo systemctl status homesuite.service --no-pager -l
journalctl -u homesuite.service -f -o cat
sudo systemctl status homesuite-console.service --no-pager -l
journalctl -u homesuite-console.service -f -o cat
```

The primary file log is `~/homesuite/homesuite.log`. Structured command-event
metadata is stored at `~/homesuite/logs/events.jsonl`. Both logs rotate by size
so a Pi does not accumulate an unbounded diagnostic history.

## Command Event Privacy

By default, the structured event log stores timestamps, source metadata,
routing outcome, action outcome, and duration. It records text length but does
not store the spoken or typed command itself.

Temporarily opt in to raw command text only when diagnosing a routing or speech
problem:

```python
# local_prefs.py
COMMAND_EVENT_LOG_STORE_TEXT = True
```

Turn it back off after collecting the evidence you need. These controls set
local rotation bounds when a deployment needs different retention:

```python
RUNTIME_LOG_MAX_BYTES = 5 * 1024 * 1024
RUNTIME_LOG_BACKUP_COUNT = 3
COMMAND_EVENT_LOG_MAX_BYTES = 2 * 1024 * 1024
COMMAND_EVENT_LOG_BACKUP_COUNT = 3
```

Set a maximum size to `0` only when an external log-management policy owns
retention; otherwise leave the bounded defaults in place.

## Support Bundles

Create a safe-to-share diagnostic archive with:

```bash
homesuite support-bundle --live
```

It creates a `.tar.gz` under `backups/` containing Doctor output, enabled role
names, package versions, service state, and log sizes. It never includes
`private_config.py`, local configuration values, tokens, raw logs, or command
text.

The management console's Diagnostics view provides the same bundle as a direct
download. The browser path adds a strict archive-member allowlist and size cap;
it does not create a second diagnostic format.

## Before and After Changes

After a configuration or code update:

1. Run `homesuite doctor --live`.
2. Run the relevant [acceptance checks](ACCEPTANCE.md).
3. Restart the service only after the safe checks pass.
4. Use `homesuite status` and one normal command from the affected interface.

Keep the Home Suite HTTP API on a trusted LAN or VPN. It listens on all network
interfaces by default so companion clients can reach it; `/health` is public
for monitoring, while state, WebSocket, manifest, and command routes require
`HOMESUITE_HTTP_API_KEY`. Do not expose the port directly to the public
internet.

The management console follows the same network rule on port `8766`. It
requires a passphrase and browser session for all configuration, diagnostics,
and command routes; only `/health` is public. It has no built-in TLS. Keep it on
a trusted LAN or VPN, or place it behind a trusted HTTPS reverse proxy. See
[CONSOLE.md](CONSOLE.md).

Console configuration changes are reviewed before they are written. For each
apply, Home Suite copies every affected file into a private timestamped
directory under `backups/console/`, then performs validated atomic writes. If a
multi-file write fails, it attempts to restore files from the pre-write
contents. The console reports which services need a restart but never restarts
them automatically as a side effect of an edit, so voice capture and active
timers are not interrupted unexpectedly. The separate first-run **Activate
Home Suite** action is explicit: it reruns required live checks, writes one
fixed marker, and lets `homesuite-runtime.path` start the runtime. It cannot
accept a shell command or arbitrary service name.
