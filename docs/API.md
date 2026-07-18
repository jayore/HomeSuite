# HTTP and WebSocket API

Home Suite's in-process companion API lets Raycast, menu-bar apps, satellites,
scripts, and custom clients use the same command and room-context runtime as
voice and local text interfaces. Telegram runs directly in the process and does
not depend on this API.

## Enable and Secure It

The server is enabled by default on TCP port `8765`:

```python
# local_prefs.py
UNIFIED_SERVER_ENABLED = True
UNIFIED_SERVER_PORT = 8765
```

Generate one shared passphrase and keep it in `private_config.py`:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

```python
HOMESUITE_HTTP_API_KEY = "your-generated-value"
```

Older installations may still use `PIPHONE_HTTP_API_KEY`. Rename that
assignment to `HOMESUITE_HTTP_API_KEY`; the runtime fallback remains only to
keep upgrades working while the configuration is migrated.

The API component fails closed when the server is enabled and the key is blank.
Home Suite's local runtime continues, but no network listener is started.

The server binds to `0.0.0.0`. Treat the key as a home-control credential, keep
port `8765` on a trusted LAN, and do not expose it directly to the internet.
Use a VPN or an authenticated reverse proxy for remote access.

## Authentication

`GET /health` and its Kubernetes-style alias `GET /healthz` are intentionally
unauthenticated for local service monitoring. Every other HTTP and WebSocket
route requires the shared key.

Preferred HTTP forms:

```text
X-API-Key: <HOMESUITE_HTTP_API_KEY>
Authorization: Bearer <HOMESUITE_HTTP_API_KEY>
```

WebSocket libraries that support headers should use one of those forms. Native
browser WebSocket clients cannot set arbitrary headers and may use:

```text
ws://homesuite.local:8765/ws?room=living_room&api_key=<URL-encoded-key>
```

Query-string credentials can appear in client history or intermediary logs, so
prefer a header whenever the client supports one.

## Routes

| Route | Authentication | Purpose |
| --- | --- | --- |
| `GET /health` | None | Process/listener health check. |
| `GET /healthz` | None | Alias for `/health`. |
| `GET /manifest` | Required | Room and client-capability manifest. |
| `GET /state/{room_id}` | Required | Current media/focus state for one configured room. |
| `POST /command` | Required | Run one natural-language command. |
| `GET /ws` | Required | Subscribe to room-state and command-ack events. |

## Send a Command

Only `text` is required:

```bash
curl -sS http://homesuite.local:8765/command \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $HOMESUITE_HTTP_API_KEY" \
  -d '{
    "text": "turn on the living room lights",
    "source_id": "raycast",
    "source_type": "remote",
    "source_room": "office",
    "target_room": "living_room",
    "request_id": "example-123",
    "response_mode": "text"
  }'
```

Accepted command-text aliases are `text`, `transcript`, and `command`.
Context fields are optional:

| Field | Meaning |
| --- | --- |
| `source_id` | Stable configured source name such as `raycast` or `http`. |
| `source_type` | Source category; inferred for known source IDs when omitted. |
| `origin` | Diagnostic origin string. Defaults to `http`. |
| `source_room` | Physical/current room associated with the source. |
| `target_room` | Explicit room for this request. |
| `effective_target_room` | Alias for `target_room`; takes precedence when both exist. |
| `request_id` | Client correlation ID echoed in the response. |
| `response_mode` | Client preference echoed in the response; defaults to `text`. |
| `stt` | Optional client-provided speech metadata object echoed in the response. |
| `timing` | Optional structured voice-event timing envelope used by satellites; echoed with the brain receive time. |

Use a stable, configured `source_id` for every turn from one client. AI history,
typed follow-up referents, and other continuity state are scoped by that source
or its configured `continuity_group`/`device_group`. Omitting `source_id`
defaults HTTP requests to the shared `http` source, so otherwise unrelated
clients can end up in the same context bubble.

Source mobility also affects conversational location context. A source marked
`mobile: False` may use the configured coarse home area for `near me`; a mobile
or unknown source is not assumed to be physically at home. Clients that know
their user's current geographic location should put it in the natural-language
request for now rather than changing the configured home coordinates.

The same policy applies to deterministic distance questions. A fixed source
may ask `how far is San Francisco?`; mobile and unknown sources should ask `how
far is San Francisco from home?` or name another origin. If the origin is
omitted, Home Suite asks for it and accepts a short source-scoped follow-up such
as `from home`.

A successful request returns the interaction result, even when no device action
occurred:

```json
{
  "ok": true,
  "handled": true,
  "action_occurred": true,
  "text": "Turned on the living room lights.",
  "response": "Turned on the living room lights.",
  "source": "device_confirm",
  "request_id": "example-123",
  "response_mode": "text",
  "context": {}
}
```

HTTP `400` means malformed JSON or missing text, `403` means authentication
failed, `404` means an unknown room on the state route, and `500` means command
or state processing failed.

## Forwarding Voice Commands

A wake-word or PTT node can use the companion API as its command boundary while
retaining its existing local audio experience:

```python
# local_prefs.py on the microphone device
COMMAND_PROCESSING_MODE = "satellite"
SATELLITE_BRAIN_URL = "http://another-homesuite.local:8765"
```

The microphone device continues to own microphone capture, wake-word/PTT behavior,
transcription, acknowledgement cues, local response speech, and barge-in. After
STT, it sends the transcript to the other Home Suite instance. That instance owns deterministic routing,
source-scoped continuity and confirmations, AI fallback, and action execution,
then returns a structured result for the microphone device to render.

By default, the request uses the satellite hostname as its stable `source_id`,
`DEFAULT_ROOM` as its physical room, and the local `HOMESUITE_HTTP_API_KEY` for
authentication. Advanced deployments can override
`SATELLITE_SOURCE_ID`, `SATELLITE_SOURCE_ROOM`, and
`SATELLITE_COMMAND_TIMEOUT_SECONDS` in `local_prefs.py`. Set
`SATELLITE_BRAIN_API_KEY` in `private_config.py` only when the other instance uses a
different companion API key.

Command forwarding fails closed. If the other instance is unreachable or rejects the
request, the microphone device uses its normal failure cue and does not retry through
its local command runtime; this prevents duplicate actions and split continuity.
Exact dismissals such as `never mind` are forwarded so the authoritative brain
can clear its pending confirmation or clarification before the satellite ends
the turn silently.

Wake-word satellites also attach a versioned `timing` envelope. It includes a
stable `utterance_id`, the wake model label and score, the timestamp of the
audio frame that produced the wake hit, the later model-decision timestamp,
capture and VAD speech boundaries, STT timing, the satellite send time, and the
brain receive time. Audio-frame timestamps come from the continuously drained
microphone ring; PortAudio's ADC clock is used when available so buffered frame
processing does not collapse the apparent timeline. The request ID uses the
same utterance ID.

Cross-node event timestamps are UTC Unix milliseconds. Durations are derived
from each node's monotonic clock and only the derived values cross the network.
Satellite and brain clock-sync state is included when systemd-timesyncd exposes
it. Keep NTP enabled on every node before enabling arbitration:

```bash
timedatectl show -p NTPSynchronized -p NTP
```

Both values should be `yes`. The brain also reports `apparent_transit_ms`, which
is useful for detecting a clock or LAN-latency outlier before trusting a
candidate in an arbitration window.

This is currently a transcript-first **voice runtime mode**, not a thin install
profile. The satellite still runs the normal Home Suite process and its local
companion API/scheduler unless those features are separately disabled. Timers,
alarms, scheduled actions, and temporary restorations created through the
satellite are owned by the brain; later alarm/audio output follows the brain's
configured output policy rather than being pushed back to the originating
satellite. Multi-satellite wake-word arbitration and deduplication are also
future work; the timing envelope is the event substrate for that next phase and
does not yet delay, suppress, or select between competing satellite requests.

## Manifest and State

`GET /manifest` returns `schema_version`, `default_room`, and a `rooms` list.
Each room includes display names, aliases, HA area, media players, brightness
target, client-visible scenes, and devices.

`GET /state/{room_id}` and WebSocket state events use this shape:

```json
{
  "event": "state",
  "room": "living_room",
  "focused_entity": "media_player.living_room",
  "focused_label": "Living Room",
  "players": [],
  "brightness_pct": 30.0
}
```

Player objects can include title, artist, album, series, app, volume, artwork,
position, duration, and update timestamps. Fields may be null when Home
Assistant does not expose them.

## WebSocket Events

Pass `room=<room_id>` in the WebSocket query to subscribe to that room. Missing
or unknown room values use `DEFAULT_ROOM`.

The server immediately sends one `state` event and then sends updated state as
relevant Home Assistant entities change. Commands completed for that room emit:

```json
{
  "event": "command_ack",
  "room": "living_room",
  "text": "pause",
  "response": "Paused the living room TV.",
  "handled": true,
  "action_occurred": true
}
```

Incoming WebSocket messages are currently ignored; send commands through
`POST /command`.

## Troubleshooting

* `curl http://localhost:8765/health` fails: confirm the service is running and
  `UNIFIED_SERVER_ENABLED` is true.
* Logs show `UNIFIED_SERVER_START_FAIL`: configure a non-empty shared key.
* HTTP or WebSocket handshake returns `403`: verify the exact same key on both
  server and client.
* State connects but remains empty: confirm Home Assistant WebSocket auth and
  room `focus_participants`/TV entities.
