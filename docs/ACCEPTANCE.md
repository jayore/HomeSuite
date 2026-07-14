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

1. Stop the service before microphone calibration.
2. Use `homesuite calibrate-mic --list-devices` and a stable `device_match`.
3. Speak at normal volume during calibration; the result should be healthy and
   free of clipping.
4. Restart the service and check that repeated handset commands report
   `PTT_AUDIO_CAPTURE overflows=0`.
5. Hang up while assistant speech is active; local speech should stop at once.

See [PTT.md](PTT.md) for audio profile details and the physical hook contract.

## Wakeword Node

1. Run `homesuite install-wakeword` and `homesuite doctor --role wakeword`.
2. Confirm the intended model label appears in the service log before changing
   thresholds.
3. Calibrate the microphone with the service stopped.
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
