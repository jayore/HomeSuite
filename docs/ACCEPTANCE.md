# First-Run Acceptance Checks

This checklist proves the parts of Home Suite that a particular node actually
uses. Work through the shared checks first, then only the role-specific section
for that device.

## Shared Checks

1. Confirm the three ignored local files exist and the active role is expected:

   ```bash
   homesuite doctor
   ```

2. Confirm Home Assistant is reachable and the entities named in your room
   configuration exist:

   ```bash
   homesuite doctor --live
   ```

3. Confirm a state query resolves against real Home Assistant state without
   changing anything:

   ```bash
   homesuite test "what lights are on?"
   ```

4. Confirm a planned write is blocked but visible:

   ```bash
   homesuite test "turn off the living room lights"
   ```

   The output should include an `HA_STUB call`, not change a light.

5. When those pass, make one deliberate live change and verify it in Home
   Assistant:

   ```bash
   homesuite test --live "turn on the living room lights"
   ```

   Use `homesuite repl` for an interactive safe shell, `homesuite repl --live`
   only for deliberate device testing, and `homesuite repl --isolated` for
   parser work that must not read Home Assistant at all.

## Integration Setup

1. Open **Integrations** and confirm cards distinguish ready, incomplete, and
   unused providers without displaying credential values.
2. Open one provider's **Manage** dialog and confirm it contains only that
   provider's settings, existing secrets are masked, and the setup guidance is
   specific to each field.
3. Change a reversible non-secret value, choose **Review changes**, and confirm
   the before/after summary is accurate. Return without applying it.
4. Run **Test** on a configured provider and confirm the result reports only a
   concise success or actionable failure; it must not include a token, raw
   provider response, or secret-bearing URL.
5. Confirm a partial provider cannot be tested until all required connection
   settings are present.

## API Node

Start the service and confirm the listener:

```bash
sudo systemctl restart homesuite.service
homesuite status
curl -sS http://localhost:8765/health
```

Use the generated `HOMESUITE_HTTP_API_KEY` only in trusted clients. Verify a
real authenticated command with the request shape in [API.md](API.md).

## Push-to-Talk Node

1. Use the management console's Audio view for guided calibration, or stop the
   service before using the headless CLI.
2. Confirm the discovered microphone uses a stable `device_match`; the CLI
   equivalent is `homesuite calibrate-mic --list-devices`.
3. Speak at normal volume during calibration; the result should be healthy and
   free of clipping.
4. Confirm **What to do next** states the measured condition and target range,
   then either recommends keeping the current level or names a specific field.
   For a concrete suggestion, confirm its compact review changes only that
   field and nothing is saved until **Apply** is selected.
5. Restart the service and check that repeated handset commands report
   `PTT_AUDIO_CAPTURE overflows=0`.
6. Hang up while assistant speech is active; local speech should stop at once.

See [PTT.md](PTT.md) for audio profile details and the physical hook contract.

## Console Activation

1. Apply a reversible node setting and confirm **Restart required** remains
   visible after refreshing the browser.
2. Open the restart dialog and confirm it names only the affected Home Suite
   service and explains the brief interruption.
3. Restart `homesuite.service`; confirm the console waits for a new systemd
   invocation and healthy HTTP endpoint before reporting success.
4. Begin calibration or active assistant audio and confirm a runtime restart is
   refused until the audio operation ends.
5. Submit an unknown service name directly to the authenticated endpoint and
   confirm it is rejected without executing a command.

## Wakeword Node

1. Run `homesuite install-wakeword` and `homesuite doctor --role wakeword`.
2. Confirm the intended model label appears in the service log before changing
   thresholds.
3. Run guided Audio calibration, or calibrate with the CLI while the service is
   stopped.
4. Capture positive and negative samples with `homesuite wakeword-lab`, replay
   them, and tune from those measurements rather than a single anecdote.
5. Test wakeword, one-breath command capture, a short pause after the wakeword,
   cancel, rearm, and local-TTS interruption separately.

See [WAKEWORD.md](WAKEWORD.md) for exact commands and interpretation.

## When Something Is Wrong

Start with:

```bash
homesuite doctor --live
homesuite logs --lines 100
homesuite support-bundle --live
```

The support bundle deliberately excludes secrets, local configuration values,
raw logs, and command text. It contains role readiness, package versions,
service state, and log sizes that are safe to share in an issue.

From the console, confirm an unhealthy test check offers a relevant setup
action, then download **Support bundle**. Inspect the archive and confirm its
only members are `README.txt`, `doctor.json`, and `summary.json`.
