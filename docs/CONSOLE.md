# Home Suite Management Console

The Home Suite Console is an authenticated browser surface for inspecting one
node, running diagnostics, and trying the shared text interaction pipeline. It
runs as a separate process from `homesuite.service` on port `8766` by default.
A console restart does not restart PTT, wake-word capture, alarms, or the live
command runtime.

The console shows effective configuration and provides guided editing for
common node, credential, and shared room settings. Its Chat surface forwards
messages to the running live command service; safe dry runs remain deliberately
separate in the CLI.

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

When installed through `scripts/install.sh --start` or `--systemd`, use the
separate unit:

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

Outside the one-time claim flow, a blank console key reuses
`HOMESUITE_HTTP_API_KEY` for compatibility with existing and manually managed
installations. Fresh native installs create a one-time claim marker. While that
marker is present, normal login is disabled and the process uses an ephemeral
internal key until the first visit creates a passphrase of at least 12
characters. The console writes it to
`HOMESUITE_CONSOLE_KEY` with the same validated backup and atomic-write path as
other console edits, removes the marker, and signs that browser in. It does not
return the passphrase in the response.

First-visit claiming is available only when the installer-created marker and a
blank saved console key are both present. Existing installations do not become
claimable merely because a field is blank. Keep a fresh unclaimed node on a
trusted LAN: the first browser to reach it can choose the passphrase.

The passphrase is exchanged for an HTTP-only, same-site browser session cookie;
browser responses never contain the configured passphrase or integration
credential values during ordinary read-only use. Restarting the console signs
out existing browser sessions.

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

* **Setup** guides a fresh node through Home Assistant, its first room, node
  roles, conditional voice/audio work, a safe test command, and guarded runtime
  activation. On a configured node, **Preview onboarding** shows the same
  journey with synthetic status and disabled actions; it never writes config or
  operates services.
* **Overview** shows hostname, revision, enabled node roles, room count,
  integration count, and local Doctor readiness.
* **Settings** shows general node behavior, assistant processing, network, and
  access settings in labeled Setting and Current value columns. Search filters
  by label, key, description, or section, while the section menu jumps through
  the longer page. **Edit settings** opens the guided editor described below.
* **Audio** shows the effective microphone profile, playback target, detected
  ALSA hardware, output testing, profile editing, and guided microphone
  calibration for this node.
* **Wake word** lists models available on this node, activates one or several
  local models, pauses wake-word listening, accepts compatible OpenWakeWord
  `.onnx` uploads by file picker or drag and drop, and exposes detector tuning.
* **Physical controls** manages PTT behavior, its BCM GPIO input and electrical
  level, wake-word coexistence, and auxiliary buttons that execute commands.
* **Rooms** shows and edits room identity, aliases, Home Assistant area,
  lighting and media targets, TV/Plex routing, and client controls.
* **Integrations** reports ready, incomplete, or not-configured status and
  provides provider-scoped setup, semantic review, and explicit connection
  tests. Overview cards never return secret values.
* **Chat** sends text through the same live deterministic and conversational
  interaction layer used by other Home Suite surfaces.
* **Diagnostics** runs Home Suite Doctor locally or with optional live network
  and topology checks, places warnings and failures first, routes unhealthy
  checks to the relevant setup view, and downloads a privacy-validated support
  bundle.

## Guided Setup And Activation

Setup is an orchestrator over the existing Settings, Integrations, Rooms,
Audio, Wake word, Physical controls, Chat, and Diagnostics surfaces.
It does not duplicate their values or write a separate onboarding
configuration. PTT and wake-word roles remain independent and can both be
enabled; the audio step appears whenever either role is active.

While initial setup is incomplete, **Setup** remains in the primary navigation.
Opening a setup step uses the full management view for that task and leaves a
slim sticky setup bar at the top of the management pane, above the normal page
header and alongside the unchanged primary navigation. The bar names the
current step and provides **Back to setup** plus an explicit exit control. It
appears only on the management view opened by that step and survives an ordinary
page reload within the browser session. Choosing another primary navigation
item or closing the bar ends that guided detour instead of carrying setup
context across unrelated pages.

Setup becomes complete when activation writes `state/setup_complete.json`.
Existing installations whose runtime is already healthy are also recognized as
complete for compatibility; the authenticated setup-status check records that
state once so it remains complete through later restarts or outages. Setup then
leaves the primary navigation after the user leaves the checklist; later runtime
failures are surfaced through Diagnostics rather than reopening onboarding.
**Overview > Review setup** temporarily reopens the checklist for hardware
changes, integration maintenance, or onboarding preview.

The final activation request has a narrow server contract:

1. run Home Suite Doctor with bounded live checks
2. reject the request when any required check fails and return that report
3. write the fixed private `state/setup_complete.json` marker
4. let the installer-owned `homesuite-runtime.path` unit start
   `homesuite.service`
5. poll the local health endpoint and report whether the runtime became healthy

The browser cannot provide a shell command, arbitrary file path, or service
name. A configured node whose runtime is already healthy is treated as setup
complete even when it predates the activation marker.

Primary page actions such as **Edit settings**, **Edit audio**, **Add room**,
and page refresh live in the sticky top bar. Provider setup and connection
tests stay on their corresponding integration cards. Actions remain available
while reviewing long pages and collapse to icons on smaller screens.
The system status at the right of the top bar is also a Diagnostics shortcut;
on narrow screens it retains a status icon and issue count instead of becoming
an unexplained color indicator.

## Edit Settings

The editor is deliberately schema-driven rather than a general Python or YAML
editor. **Settings** covers general node identity and behavior, assistant
processing, network ports, and core access credentials. The same validation and
review system powers focused editors in **Physical controls**, **Wake word**,
and **Integrations**, so a value has one logical home without creating separate
configuration stores. Each control includes:

* a plain-language description
* an example or expected-format placeholder
* setup guidance and a relevant documentation path when available
* the current value, or configured/not-configured status for credentials

Read-only configuration responses redact credentials. Entering an authenticated
edit view loads credentials for that surface into masked fields over a
same-origin request; the eye button can reveal a value when needed. Leaving Edit
mode with **Cancel**, signing out, or losing the session removes those values
from the active configuration state. Optional private settings can be cleared,
and device overrides can be reset to their inherited value when one exists.

Because Edit mode can display working credentials, treat an unlocked console
session like access to `private_config.py`: use it only on a trusted device and
LAN or VPN, and sign out on a shared browser.

## Manage Integrations

Each integration uses a distinct semantic Lucide icon and provider-associated
accent color. The icon set is bundled with the console, so the page does not
depend on a CDN.

Each device-scoped integration card opens a focused setup dialog containing
only that provider's settings. The controls, descriptions, placeholders, and
credential guidance come from the same schema as Settings and the other guided
editors. Saves use the same type and URL validation, semantic preview,
stale-revision protection,
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

## Manage Wake Word

The **Wake word** view is the preferred place to manage model selection. It
discovers `.onnx` files already configured on the node and files in Home
Suite's local wake-model directories. Select **Listen for this** on as many
local models as the device should recognize. A node can therefore respond to
both a primary phrase and an alternate phrase without running a second
listener.

Drop a compatible OpenWakeWord `.onnx` file onto **Add your own model**, or use
the file picker. The console validates the file in a bounded subprocess and
stores it under the ignored local `wake_models/` directory. Uploading adds an
available option; it does not silently activate the model. Duplicate uploads
are deduplicated, while a genuinely different file with the same name receives
a short content suffix.

Choose **Review changes** before the selection is written. Saving updates the
same `WAKEWORD_ENABLED`, `WAKEWORD_MODEL`, and `WAKEWORD_MODEL_PATHS` values
used by the voice runtime. The running detector keeps its current selection
until **Restart required** restarts `homesuite.service`. Pausing listening does
not delete model files. An uploaded model must be deactivated and saved before
its Remove button becomes available.

OpenWakeWord's bundled selection and local custom models use different loading
modes in the current runtime. Choosing a local model replaces a bundled
selection, and choosing the bundled model replaces local selections; multiple
local models can be active together. **Tune detector** edits the engine,
threshold, and VAD settings on the same Wake word page. More advanced timing
and audio-path behavior is documented in [WAKEWORD.md](WAKEWORD.md).

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

The effective default room is still a per-node setting under Settings.
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

## Console Setting Ownership

The browser groups settings by what the user is configuring, not by the Python
file that stores them:

* **Settings** owns general node identity and behavior, assistant processing,
  network ports, and access settings.
* **Physical controls** owns the PTT switch, BCM GPIO pin, electrical listen
  level, submit-or-cancel behavior, cue delay, wake-word coexistence policy,
  and auxiliary command-button wiring and gesture maps. Command buttons execute
  commands and never control microphone capture. A purpose-built mapper keeps
  button IDs, BCM pins, and gesture commands aligned.
* **Wake word** owns listening state, model selection and upload, detector
  engine, threshold, and VAD tuning. One or several compatible models can be
  active.
* **Integrations** owns provider credentials and connection testing. **Audio**
  owns microphone, local signal processing, playback, and calibration. **Rooms**
  owns the shared room topology.

The contract is that normal setup and maintenance settings are visible in the
console. Simple values receive guided controls, nested values receive
purpose-built editors, and low-level tuning remains grouped under **Advanced**
until it has a safe control. Deprecated compatibility aliases such as the original `HANDSET_*`
names remain loadable but do not appear as duplicate user-facing settings.

The **Settings coverage** summary reports actionable configuration issues,
guided settings owned by that page, and advanced settings currently in use.
Settings sections remain expanded so view and edit modes preserve the same
page structure. Assistant context and common calendar policy now edit shared
`deployment_config.py` values through the same reviewed, backed-up write path.
The Advanced inventory lists effective overrides that do not yet have a safe
control and opens their relevant documentation. Deprecated compatibility aliases and unrecognized
assignments appear separately as settings needing attention. Credential values
remain redacted in this inventory.

Configuration ownership follows the console's feature pages rather than one
global advanced-settings form. Wake-word detection, listening behavior,
transcription, and timing live under **Wake Word → Settings**. Integration
behavior lives beside its credentials in the integration's **Manage** dialog;
for example, YouTube playlist refresh is a device-local YouTube setting.
The Settings-page Advanced inventory remains a reference for supported values
that do not yet have a safe domain control and does not duplicate owned fields.

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
catalogs and expert policies without guided controls remain direct file edits;
none of these structured settings are exposed as unvalidated free-form text
fields. Likely future owners include Diagnostics for privacy and retention,
language tooling for aliases and pronunciation, and each integration for its
own advanced thresholds. The console's own bind address and port also remain a
deliberate file-managed recovery setting so a browser edit cannot silently lock
the user out of the console.

Integration readiness is local to the node serving the console. A credential
configured on one Pi does not make it available to another Pi unless the same
private configuration or environment is intentionally provided there.

## Diagnostics And Support

Doctor warnings and failures retain their redacted technical detail and add a
plain next step when the console can identify the owning surface. **Open
Integrations**, **Open Audio**, **Open Rooms**, **Open Physical controls**,
**Open Wake word**, or **Open Settings** moves directly to that setup view;
provider actions also bring the corresponding integration card into view.
Groups and checks containing warnings or failures sort ahead of healthy results,
and the report summary jumps to the first issue. Rerun local or live checks after
making and activating a correction.

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

## Chat

Chat is a live browser interface to Home Suite. Messages are forwarded to the
authenticated `POST /command` endpoint owned by the running
`homesuite.service`, so they can control devices and create persistent actions.
Keeping execution in the production process preserves command ordering and
dialogue state across follow-up messages.

The browser uses one stable session ID so references such as "turn it off" stay
scoped to that browser conversation. Clearing visible messages clears only the
browser transcript. If the runtime or its HTTP API is unavailable, Chat reports
that availability error rather than running a second command engine inside the
management process.

The Location control defaults to **Follow conversation**. In that mode, phrases
such as "I'm in the kitchen now" set a sticky room for this browser session, and
later room-relative commands follow it just as they do in Telegram or Raycast.
Selecting a named room pins requests to that room until **Follow conversation**
is selected again.

Safe capture remains available to developers through `homesuite test` and
`homesuite repl`. Those tools read current Home Assistant state while blocking
writes and persistent actions; they are deliberately separate from browser
Chat.

## Scope

The console is a configuration and management surface, not a Home Assistant
dashboard or a second set of device-control tiles. Chat remains available as a
simple first interface, for setup and regression checks, and for continued text
interaction when it is convenient.

Use the guided Settings, Physical controls, Wake word, Integrations, Audio, and
Rooms editors for the fields they expose.
Continue editing `deployment_config.py` directly for shared policies that do
not yet have controls, and use `local_prefs.py` directly for advanced
per-node values not yet in the schema. The console preserves unrelated code
and comments when it changes an existing assignment.
