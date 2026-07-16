# Home Suite Management Console

The Home Suite Console is an authenticated browser surface for inspecting one
node, running diagnostics, and trying the shared text interaction pipeline. It
runs as a separate process from `homesuite.service` on port `8766` by default.
A console restart does not restart PTT, wake-word capture, alarms, or the live
command runtime.

The console shows effective configuration and provides guided editing for a
curated set of common node, credential, and shared room settings. The separate
text surface has the Test and explicit Live behavior described below.

## Start the Console

Run it in the foreground:

```bash
cd ~/homesuite
homesuite console
```

Then open:

```text
http://<homesuite-host>:8766
```

When installed through `scripts/install.sh --systemd`, use the separate unit:

```bash
sudo systemctl start homesuite-console.service
sudo systemctl status homesuite-console.service --no-pager -l
journalctl -u homesuite-console.service -f -o cat
```

The default listener is `0.0.0.0:8766`. Override `CONSOLE_HOST` or
`CONSOLE_PORT` in `local_prefs.py` for one node. Binding to `127.0.0.1` is a
good choice when access will only use an SSH tunnel.

## Sign In

The console uses this optional private setting:

```python
HOMESUITE_CONSOLE_KEY = "a-separate-long-random-value"
```

When it is blank, the console reuses `HOMESUITE_HTTP_API_KEY`. Fresh native
installs already generate that API key. The passphrase is exchanged for an
HTTP-only, same-site browser session cookie; browser responses never contain
the configured passphrase or integration credential values during ordinary
read-only use. Restarting the console signs out existing browser sessions.

`HOMESUITE_CONSOLE_KEY` and `HOMESUITE_HTTP_API_KEY` environment variables take
precedence over the corresponding private-config values when a service manager
or container supplies credentials that way.

Generate a separate key with:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

The console is designed for a trusted home LAN or VPN. It does not provide TLS
and should not be port-forwarded directly to the public internet. Use a trusted
reverse proxy with HTTPS if remote browser access is required.

## Current Views

* **Overview** shows hostname, revision, enabled node roles, room count,
  integration count, and local Doctor readiness.
* **Configuration** shows a curated set of effective node settings in labeled
  Setting and Current value columns. Search filters by label, key, description,
  or section, while the section menu jumps through the longer page. **Edit
  configuration** opens the guided editor described below.
* **Audio** shows the effective microphone profile, playback target, detected
  ALSA hardware, output testing, profile editing, and guided microphone
  calibration for this node.
* **Rooms** shows and edits room identity, aliases, Home Assistant area,
  lighting and media targets, TV/Plex routing, and client controls.
* **Integrations** reports ready, incomplete, or not-configured status and
  provides provider-scoped setup, semantic review, and explicit connection
  tests. Overview cards never return secret values.
* **Test Console** runs text through the same deterministic and conversational
  interaction layer used by other Home Suite surfaces.
* **Diagnostics** runs Home Suite Doctor locally or with optional live network
  and topology checks, places warnings and failures first, routes unhealthy
  checks to the relevant setup view, and downloads a privacy-validated support
  bundle.

Primary page actions such as **Edit configuration**, **Edit audio**, **Add room**,
and page refresh live in the sticky top bar. Provider setup and connection
tests stay on their corresponding integration cards. Actions remain available
while reviewing long pages and collapse to icons on smaller screens.
The system status at the right of the top bar is also a Diagnostics shortcut;
on narrow screens it retains a status icon and issue count instead of becoming
an unexplained color indicator.

## Edit Configuration

The editor is deliberately schema-driven rather than a general Python or YAML
editor. It currently covers common node identity, runtime roles, wake-word and
audio behavior, PTT and auxiliary GPIO inputs, assistant settings, network
ports, core credentials, and optional integration credentials. Each control
includes:

* a plain-language description
* an example or expected-format placeholder
* setup guidance and a relevant documentation path when available
* the current value, or configured/not-configured status for credentials

Read-only configuration responses redact credentials. Entering **Edit
configuration** loads existing credentials into masked fields over an authenticated,
same-origin request; the eye button can reveal a value when needed. Leaving
Edit mode with **Cancel**, signing out, or losing the session removes those
values from the active configuration state. Optional private settings can be
cleared, and device overrides can be reset to their inherited value when one
exists.

Because Edit mode can display working credentials, treat an unlocked console
session like access to `private_config.py`: use it only on a trusted device and
LAN or VPN, and sign out on a shared browser.

## Manage Integrations

Each integration uses a distinct semantic Lucide icon and provider-associated
accent color. The icon set is bundled with the console, so the page does not
depend on a CDN.

Each device-scoped integration card opens a focused setup dialog containing
only that provider's settings. The controls, descriptions, placeholders, and
credential guidance come from the same schema as Configuration. Saves use the
same type and URL validation, semantic preview, stale-revision protection,
private backup, Python compilation, and atomic write path; the Integrations
view is not a second configuration store.

Existing credentials are loaded into masked fields only after an authenticated
same-origin request. The eye control reveals a value when needed. Closing the
dialog, signing out, or losing the session clears those values from the active
browser state. Integration cards expose only readiness counts and never return
credential values.

**Test** performs one bounded, non-mutating provider request using the current
saved values. Tests do not follow redirects, return provider response bodies,
or include credentials and service URLs in errors. OAuth providers validate an
existing refresh grant without changing scopes; smart-home and homelab tests
use read-only identity, status, or count endpoints. A successful test proves
the provider accepted the saved connection from this node, not that every
command contract is enabled or correctly mapped.

Porcupine has no standalone network test because its access key is validated
when that wake-word runtime starts. Weather, astronomy, and calendar remain
deployment-scoped integrations and link to their shared configuration guidance
instead of pretending to be per-device credential sets.

Choose **Review changes** to see a semantic before/after summary. Secret
summaries say only configured, replaced, or cleared. Applying a reviewed change:

1. rejects files that changed since the review
2. validates types, ranges, choices, URLs, ports, and lockout-sensitive keys
3. compiles the proposed Python files before writing
4. copies affected files to a private timestamped directory under
   `backups/console/`
5. writes `local_prefs.py` and/or `private_config.py` atomically

The console does not restart either Home Suite service as part of a save. It
records which service must reload and presents a sticky **Restart required**
action instead. This keeps a configuration edit from unexpectedly interrupting
PTT, wake-word capture, speech, alarms, or timers.

## Activate Saved Changes

**Restart required** remains visible across page reloads and console restarts.
The pending state is stored in `state/console_restart_required.json` and clears
only after the console observes a healthy service with a new systemd invocation.
A manual `systemctl restart` therefore clears the same marker once the console
sees the new process.

The restart control has a deliberately narrow contract. It recognizes only
`homesuite.service` and `homesuite-console.service`, signals the exact systemd
main process, and requires that process to run as the console user with
`Restart=always`. It cannot execute shell commands, invoke `sudo`, or accept an
arbitrary service name. A runtime restart is blocked while calibration, command
capture, or assistant audio is active.

After restarting `homesuite.service`, the console waits for a new invocation
and a healthy local HTTP endpoint before reporting success. Restarting
`homesuite-console.service` ends the authenticated browser session; the page
waits for the console to return and then shows sign-in again.

## Edit Rooms

The Rooms editor updates the canonical shared `ROOMS` assignment in
`deployment_config.py`. Each room keeps one stable ID and can configure:

* a display name, spoken aliases, and Home Assistant area
* disabled, area-based, selected-light, or proxy/helper room brightness
* room color and volume targets
* primary audio, announcements, Spotify Connect names, and media focus
* TV, remote, power-on scene, Plex client, and Plex launch script
* client-visible media players, devices, shortcuts, and named audio outputs

Home Assistant areas and entity IDs are offered as optional browser
suggestions when HA credentials and registry access are working. Every field
also accepts a manual ID, so HA discovery is never required. Existing values
that are not yet represented by a guided control are retained when a room is
saved.

**Add room**, Edit, Duplicate, and Remove only change a browser draft. Choose
**Review changes** before anything is written. The server then validates room
IDs, spoken-name collisions, target shapes, allowed HA domains, and the active
default-room reference. It rejects a stale review if `deployment_config.py`
changed in the meantime, creates a timestamped backup under
`backups/console/`, compiles the proposed Python, and writes atomically.

The editor deliberately preserves an explicit proxy such as
`light.living_room_brightness`; it never converts a proxy room to HA-area
control. Existing room IDs are locked because changing one can invalidate
saved focus and source mappings. Add a new room or alias and migrate
deliberately instead.

The effective default room is still a per-node setting under Configuration.
The Rooms editor will not remove that room. Change the node default first when
retiring it. Restart `homesuite.service` after applying room changes so the
voice and companion command runtime reload the shared topology.

## Manage Audio

The Audio view is device-local. It expands sparse `AUDIO_INPUT_PROFILE`
settings into their effective values, discovers capture and playback hardware,
and displays stable ALSA card IDs such as `Device` or `MINI` instead of relying
on card numbers that can change after a reboot or USB reconnect.

**Edit audio** manages microphone selection, native sample rate and channels,
PortAudio latency, strict device matching, optional ALSA hardware-gain
enforcement, per-path software processing, and the local playback target. A
blank playback override preserves the current service or system setting. The
editor retains profile fields it does not yet recognize, validates the complete
profile, creates a private backup, and writes `local_prefs.py` atomically after
review. After applying an audio change, use the sticky **Restart required**
action when you are ready to reload `homesuite.service`.

**Test** plays the normal wake cue through the displayed output without saving
a change. Guided calibration records a short room-noise sample and a normal
speech sample, then reports noise floor, speech peak and level, clipping, and
dropped input blocks. Results include a **What to do next** section that first
states what was measured and whether it was too high, too low, noisy, or
dropping audio. It then names the relevant field and target range.

When a recommendation has one concrete, bounded value, such as hardware gain
`7` to `6` or wake-word stream latency Low to High, **Apply suggestion** opens a
compact before/after confirmation. Confirming it saves only that field through
the same validation, stale-revision check, private backup, and atomic write as
the full Audio editor. Recommendations that require hardware choice or human
judgment continue to open the relevant Edit control. Calibration capture never
changes a setting by itself, and neither path restarts a service automatically.
Use **Restart required** to restart `homesuite.service`, then rerun calibration
to verify a saved suggestion.

On a wake-word node the running service briefly stops its continuous capture
stream; on a PTT node it prevents a new PTT session. Voice capture resumes after
completion, cancellation, failure, browser navigation, or the bounded lease
timeout. Calibration refuses to begin during an active command, PTT session,
or spoken response.

The same Audio view supports PTT-only, wake-word-only, and combined nodes.
Signal-processing values remain independently scoped inside the shared profile:
wake-word detection, command transcription, and PTT each consume their own
documented fields.

## Configuration Organization

The browser groups settings by what the user is configuring, not by the Python
file that stores them:

* **Node and role** contains identity and top-level interaction capabilities.
* **Push-to-talk (PTT)** contains the PTT switch, BCM GPIO pin, electrical
  listen level, submit-or-cancel behavior, cue delay, and wake-word coexistence
  policy.
* **Wake word** contains detector, model, and threshold settings.
* **Additional GPIO buttons** contains command-button wiring and gesture maps.
  These buttons execute commands and never control microphone capture. A
  purpose-built mapper keeps button IDs, BCM pins, and gesture commands aligned.
* Assistant, network, and integration sections own their corresponding runtime
  settings and credentials. The dedicated Audio view owns microphone and local
  playback setup.

The long-term contract is that supported per-node settings should be visible in
the console. Simple values receive guided controls, nested values receive
purpose-built editors, and low-level tuning belongs in collapsed advanced
sections. Deprecated compatibility aliases such as the original `HANDSET_*`
names remain loadable but do not appear as duplicate user-facing settings.

The **Configuration status** summary reports actionable configuration issues,
guided settings, and advanced settings currently in use. The advanced
inventory lists every effective file-managed override and opens its relevant
documentation. Deprecated compatibility aliases and unrecognized assignments
appear separately as settings needing attention. Credential values remain
redacted in this inventory.

The inventory treats the three example files as the documented public
configuration contract. This keeps `local_prefs.py`, `deployment_config.py`,
`private_config.py`, Doctor, and the browser accountable to the same set of
supported settings while purpose-built editors are added incrementally.

The additional-button editor presents one row per wired control. It supports
single press, double press, long press, multiple sequential command phrases,
and held-button repetition. Review still shows two underlying settings because
the runtime deliberately retains the established `PHYSICAL_BUTTON_PINS` and
`PHYSICAL_BUTTON_ACTIONS` dictionary contract. Existing advanced action
metadata is preserved unless that gesture is edited.

Structured `ROOMS` mappings use the purpose-built Rooms editor, and
`AUDIO_INPUT_PROFILE` uses the purpose-built Audio editor. Deployment-wide
policies without guided controls remain direct file edits; none of these
structured settings are exposed as unvalidated free-form text fields.

Integration readiness is local to the node serving the console. A credential
configured on one Pi does not make it available to another Pi unless the same
private configuration or environment is intentionally provided there.

## Diagnostics And Support

Doctor warnings and failures retain their redacted technical detail and add a
plain next step when the console can identify the owning surface. **Open
Integrations**, **Open Audio**, **Open Rooms**, or **Open Configuration** moves
directly to that setup view; provider actions also bring the corresponding
integration card into view. Groups and checks containing warnings or failures
sort ahead of healthy results, and the report summary jumps to the first issue.
Rerun local or live checks after making and activating a correction.

**Support bundle** runs the same established bundle builder as
`homesuite support-bundle`. If the current report is live, the downloaded
bundle includes the same bounded live checks. Before serving the archive, the
console enforces a 2 MB compressed limit and an exact allowlist of
`README.txt`, `doctor.json`, and `summary.json`. Generation fails closed if an
unexpected file or archive path appears.

The archive excludes credentials, local configuration values, entity and room
identifiers from Doctor details, raw logs, and command text. It is designed to
be attached to a support issue, but review any artifact before sharing it
outside the trusted environment.

## Test And Live Messages

The text console always opens in **Test** mode.

Test mode uses Home Suite's established capture runtime. It reads current Home
Assistant state for realistic routing but blocks Home Assistant writes and
persistent timers, alarms, schedules, and temporary restorations. It can still
make configured read-only network calls and AI requests, so provider usage may
still occur. The same runtime policy blocks direct applet lifecycle changes,
Spotify library writes, YouTube playback and registry changes, Plex playback,
announcements, and qBittorrent pause commands. Every result remains labeled as
a Test preview.

**Live** mode is an explicit toggle with a confirmation. Live messages are
forwarded to the authenticated `POST /command` endpoint owned by the running
`homesuite.service`; they can control devices and create persistent actions.
This keeps live command ordering and dialogue state in the production process.
If that service or its HTTP API is unavailable, Live returns an availability
error while Test remains usable.

Test and Live keep separate runtime dialogue state. The browser uses one stable
session ID so follow-up references remain scoped to that console session within
the selected mode. Clearing visible messages only clears the browser transcript.

## Scope

The console is a configuration and management surface, not a Home Assistant
dashboard or a second place to control individual devices. Live text remains
available because it is useful for setup, regression testing, and occasional
interaction, but the console does not render device-control tiles.

Use the guided Configuration, Audio, and Rooms editors for the fields they expose.
Continue editing `deployment_config.py` directly for shared policies that do
not yet have controls, and use `local_prefs.py` directly for advanced
per-node values not yet in the schema. The console preserves unrelated code
and comments when it changes an existing assignment.
