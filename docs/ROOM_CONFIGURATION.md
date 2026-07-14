# Room Configuration

Fresh public installs keep shared, non-secret home topology in `ROOMS` inside
the ignored `deployment_config.py`. This is the canonical deployment place to
describe rooms, Home Assistant targets, media devices, spoken aliases, and
client-visible controls without modifying the upstream source checkout.

`home_registry.py` provides lookup and validation helpers. Do not create a
second room mapping there. Per-device microphone, wake-word, handset, and audio
hardware settings still belong in `local_prefs.py`.

`app_config.py` still supplies tracked defaults and remains the topology source
for older/private deployments that intentionally version their complete home
configuration. `deployment_config.py` overrides those defaults before
per-device `local_prefs.py` is applied. Secrets never belong in a room object.

## Existing Deployment Migration

Existing installations without `deployment_config.py` continue using
`app_config.py` unchanged. To migrate deliberately:

1. Copy `deployment_config.example.py` to `deployment_config.py`.
2. Replace its sample `DEFAULT_ROOM`, `ROOMS`, location, and label values with
   the corresponding deployment values from `app_config.py`.
3. Run `homesuite-doctor` and compare room/target output before restarting.
4. Keep the real deployment file ignored and distribute it privately to other
   Home Suite devices.

Do not accept the generic example over an existing working topology.

## IDs And Objects

`DEFAULT_ROOM` contains a stable room ID:

```python
DEFAULT_ROOM = "living_room"
```

The corresponding room object is `ROOMS["living_room"]`. Runtime code uses
`get_default_room_id()` when it needs the ID and `get_default_room()` when it
needs the object.

Use short, stable, lowercase IDs with underscores. Human-facing names belong
in `label` and `aliases`:

```python
"living_room": {
    "label": "Living Room",
    "ha_area_id": "living_room",
    "aliases": ["living room", "lounge"],
    ...
}
```

Changing a room ID affects saved room focus and source mappings. Prefer adding
an alias when only the spoken wording needs to change.

## Minimal Room

A room does not need to support every capability:

```python
"guest_room": {
    "label": "Guest Room",
    "ha_area_id": "guest_room",
    "aliases": ["guest room"],
    "defaults": {
        "brightness_target": {"type": "area"},
        "color_light": None,
        "volume_target": None,
        "audio_output": None,
        "announcements": None,
        "spotcast_device_name": None,
        "tv": None,
        "tv_remote": None,
        "tv_on_scene": None,
        "plex_client_name": None,
        "plex_launch_script": None,
    },
    "media_players": [],
    "audio_outputs": [],
    "audio_aliases": {},
    "focus_participants": [],
    "scenes": [],
    "devices": [],
}
```

## Disabling Capabilities

Use `None` to explicitly disable one optional value or target:

```python
"color_light": None,
"brightness_target": None,
"volume_target": None,
"spotcast_device_name": None,
"tv": None,
```

Use empty containers for optional collections:

```python
"media_players": [],
"audio_outputs": [],
"audio_aliases": {},
"focus_participants": [],
"scenes": [],
"devices": [],
```

Avoid empty strings. They are often normalized as missing, but `None` states
the intent clearly and is the consistently supported representation.

Avoid commenting out a target when the goal is to disable it. Omission can
activate compatibility behavior:

* Missing `brightness_target` may fall back to legacy `brightness_number` or
  `brightness_light`.
* Missing `volume_target` may fall back to legacy `volume_number` or the room's
  `audio_output`.
* Explicit `brightness_target: None` or `volume_target: None` disables that
  room-level capability.

## Field Reference

Top-level room fields:

| Field | Purpose |
| --- | --- |
| `label` | Human-facing room name used by clients and responses. |
| `ha_area_id` | Home Assistant area used by area-based lighting. |
| `aliases` | Additional spoken names that resolve to this room. |
| `defaults` | Room-local targets used when no specific device is named. |
| `media_players` | Ordered, labeled players shown in client manifests. |
| `audio_outputs` | All speaker entities associated with the room. |
| `audio_aliases` | Named secondary outputs such as `bookshelf`. |
| `focus_participants` | Players considered when detecting active room media. |
| `scenes` | Client buttons backed by commands, scenes, or scripts. |
| `devices` | Client-visible entities with live state and toggle behavior. |

Fields inside `defaults`:

| Field | Purpose |
| --- | --- |
| `brightness_target` | Proxy, area, or explicit lights for room brightness. |
| `color_light` | Light or proxy used by room-level color shorthand. |
| `volume_target` | Helper or media player used by room volume commands. |
| `audio_output` | Primary media player for music and media routing. |
| `announcements` | Media player used for room announcements. |
| `spotcast_device_name` | Exact provider device name expected by Spotcast. |
| `spotcast_device_aliases` | Spoken aliases for that Spotcast device. |
| `tv` | Room television media-player entity. |
| `tv_remote` | Matching Home Assistant remote entity. |
| `tv_on_scene` | Optional scene used to power on the TV setup. |
| `plex_client_name` | Exact Plex client name for this room. |
| `plex_launch_script` | Optional Home Assistant script that launches Plex. |

The older `lights`, `brightness_number`, `brightness_light`, and
`volume_number` fields are compatibility settings. New configurations should
use `brightness_target` and `volume_target`.

## Brightness Targets

Use a proxy or helper:

```python
"brightness_target": {
    "type": "entity",
    "entity_id": "light.living_room_brightness",
}
```

`light.*`, `number.*`, and `input_number.*` entities are supported.

Use every light assigned to `ha_area_id`:

```python
"brightness_target": {
    "type": "area",
}
```

Override the room's area for this target:

```python
"brightness_target": {
    "type": "area",
    "area_id": "downstairs",
}
```

Use only selected lights:

```python
"brightness_target": {
    "type": "entities",
    "entity_ids": [
        "light.ceiling",
        "light.floor_lamp",
    ],
}
```

Area control is convenient but opt-in because an HA area can contain
decorative, grouped, or non-dimmable lights.

## Volume Targets

Use a helper when Home Assistant automation distributes room volume:

```python
"volume_target": {
    "type": "entity",
    "entity_id": "number.living_room_volume",
}
```

Use a media player for direct control:

```python
"volume_target": {
    "type": "entity",
    "entity_id": "media_player.bedroom",
}
```

Both bare commands such as `volume 30` in the active room and explicit
commands such as `set bedroom volume to 30` use that room's target. Mute and
unmute still require a real `media_player.*` because numeric helpers do not
expose media mute services.

For room-level brightness and volume, target precedence is: an explicitly
named room, then the active request/source room, then `DEFAULT_ROOM`.

## Media And Provider Names

These names serve different systems and do not need to match:

```python
"audio_output": "media_player.living_room",
"spotcast_device_name": "Livingroom",
"plex_client_name": "Apple TV",
```

* `audio_output` is a Home Assistant entity ID.
* `spotcast_device_name` is the exact Spotify Connect device name expected by
  Spotcast.
* `plex_client_name` is the exact Plex client name.

Flat values such as `SONOS_PLAYERS`, `DEFAULT_SONOS_ROOM`,
`APPLE_TV_ENTITY`, and `APPLE_TV_REMOTE` are compatibility views derived from
`ROOMS`. Configure the room object instead of editing those generated views.

## Sources And Room Focus

`SOURCES` in `app_config.py` describes where requests originate. A stationary
source can inherit `DEFAULT_ROOM`:

```python
"default_piphone": {
    "room": None,
    "inherit_default_room": True,
    "mobile": False,
    ...
}
```

Mobile sources may carry or remember room focus. Once Home Suite resolves the
request room, brightness, color, volume, media, alarms, TV, and announcements
consult the same room configuration.

Structured conversational continuity is scoped the same way. Sources use their
own ID by default; sources sharing `device_group` also share a context bubble.
Set an optional `continuity_group` when dialogue continuity should be shared
independently of room-focus grouping. The local PTT and wakeword paths both use
`default_piphone`, so they intentionally share follow-up context.

## Applying Changes

After editing `deployment_config.py` (or tracked `app_config.py` in a private
source-of-truth deployment):

1. Check syntax and configuration with `homesuite-doctor`.
2. Use `pptest` for safe parsing checks.
3. Use `pplive` for a small number of deliberate Home Assistant actions.
4. Restart `homesuite.service` so room configuration is reloaded.
5. Verify bare commands in the default room and explicit commands for each
   newly configured room.

Useful smoke tests include:

```text
brightness 40
set guest room brightness to 40
volume 20
set bedroom volume to 20
set color to red
turn on the TV
```

Unsupported capabilities should remain unclaimed or return a not-configured
response. They must not silently target another room.
