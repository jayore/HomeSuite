# FAQ

## Does AI Control My Home?

Not directly. Home Suite first uses a deterministic natural-language processing layer to parse and route commands. AI can help with conversation, summarization, and interpreting context, but actual home actions should still route through code paths that can be tested with:

```bash
pptest
```

Then type the phrase you want to test. For a single reproducible command, use `pptest "your phrase"`.

That design is intentional. It keeps real device control easier to inspect, test, and debug. It also means routine home-control commands usually do not spend AI tokens or wait on an AI response.

## Why Not Let AI Execute Actions Directly?

Because home control benefits from being boring in the best way: explicit, predictable, testable, and conservative.

Home Suite's deterministic natural-language layer handles most commands without AI. That has practical benefits:

* fewer AI calls and lower token usage
* faster responses for common commands
* more predictable routing
* easier debugging with `pptest`
* a smaller security surface for real device actions

AI is still useful for conversation, summaries, ambiguous references, and media/context interpretation. It just does not get to bypass the command layer and operate devices on its own.

## Why Not Just Use Home Assistant Entity IDs?

You can, and Home Assistant remains the underlying source of truth. Home Suite adds the plain-English layer on top: rooms, aliases, defaults, scenes, scripts, media focus, room focus, and follow-up context.

The goal is that day-to-day commands can sound like `turn off the downstairs lights`, `play music here`, or `announce dinner is ready in the kitchen` instead of forcing you to remember exact entity IDs or dashboard paths.

## Why Does Home Suite Depend So Much On Home Assistant?

Home Assistant is the source of truth for devices, rooms, scenes, scripts, and a lot of service state. Home Suite works best when Home Assistant already has clean area names, entity names, scenes, scripts, and integrations.

When in doubt, make the thing sensible in Home Assistant first. Add direct Home Suite API credentials only when they unlock something Home Assistant does not expose well.

## What Should I Run First?

After install and config edits:

```bash
homesuite-doctor
homesuite-doctor --live
pptest
```

Then type `what lights are on?` at the prompt. For one-shot debugging, `pptest "what lights are on?"` also works.

Use `pptest` and `ppchattest` while setting up. Use `pplive`, `ppchat`, or the systemd service only when you are ready for commands to affect real devices.

## What Is The Difference Between `private_config.py` And `local_prefs.py`?

`private_config.py` is for deployment-wide private values:

* Home Assistant URL and token
* OpenAI API key
* service URLs and API keys
* HTTP API keys

`local_prefs.py` is for one device:

* default room
* audio output mode
* wake-word behavior
* handset or push-to-talk hardware
* speaker routing defaults

If you eventually run multiple Home Suite devices, they may share similar `private_config.py` values but have different `local_prefs.py` files.

## Do I Need Every Service In The Example Config?

No. Home Suite is designed around optional integrations. Start with Home Assistant, an OpenAI API key, and local device preferences. Leave Plex, Spotify, Telegram, Uptime Kuma, qBittorrent, Seerr, YouTube, and wake-word settings blank until you actually use them.

Run:

```bash
homesuite-doctor
```

Blank optional services should show as `SKIP`, not `FAIL`.

## Can I Run Home Suite Without Voice Hardware?

Yes. Start with text:

```bash
pptest
ppchattest
```

Voice, wake word, handset behavior, and Sonos-routed speech can be added later.

## Can I Use It From Other Apps?

Yes. Home Suite exposes an HTTP/WebSocket API when the server is enabled. Companion clients should send commands to the same core runtime rather than reimplementing logic.

The important endpoint for simple clients is:

```text
POST /command
X-API-Key: <HOMESUITE_HTTP_API_KEY>
```

## Should I Use Docker?

The first supported public-alpha install path is native Raspberry Pi OS or Debian-like Linux. Docker may be useful later for a central server role, but Home Suite currently has local audio, optional GPIO, wake-word, and systemd assumptions that are simpler to support natively first.

## Why Did A Command Say An Integration Is Not Configured?

That usually means the command routed correctly, but the matching optional service is blank in `private_config.py`. For example, `watch The Matrix` needs Plex, and `save this song` needs Spotify Web API credentials.

Check:

```bash
homesuite-doctor
```

Then see [INTEGRATIONS.md](INTEGRATIONS.md) for the keys that service needs.

## Where Should I Look When Something Behaves Weirdly?

Use this loop:

1. `homesuite-doctor --live`
2. `pptest`, then type the exact phrase, or run `pptest "the exact phrase"`
3. `logs/`
4. Home Assistant entity/service names

If `pptest` shows that the natural-language router claimed the phrase, debug that integration. If it falls through to conversation, the router probably did not recognize the phrase as an action.
