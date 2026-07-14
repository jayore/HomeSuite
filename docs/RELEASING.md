# Release Checklist

This checklist is for maintainers preparing a beta release. A release should be
boring: tested code, clear upgrade notes, and a rollback point before it reaches
household devices.

1. Start from a clean branch and run the full test suite in a configured
   environment:

   ```bash
   .venv/bin/python -m unittest discover -s tests -p 'test_*.py'
   .venv/bin/python tools/check_docs.py
   ```

   Confirm the GitHub Actions portable matrix is also green on CPython 3.9 and
   3.13. CI is necessary but does not cover Pi audio, GPIO, or deployment
   credentials.

2. Review [Acceptance checks](ACCEPTANCE.md) and run the relevant text/API,
   PTT, and wakeword checks on real supported hardware.
3. Run `homesuite doctor --live` on each representative node and create a
   redacted `homesuite support-bundle --live` only if diagnostics need review.
4. Summarize user-visible behavior, configuration changes, migration steps,
   and known limits in the release notes. Do not include credentials, room
   names, raw logs, or command transcripts.
5. Commit the release contents, tag the commit, and push the tag to the public
   release remote after confirming the private mirror is current.
6. Update one non-critical node with `bash scripts/update.sh`, verify it, then
   roll out to the remaining nodes. Keep the prior tag available for rollback.

The GitHub workflow is a baseline guardrail, not a replacement for hardware
validation. Audio drivers, GPIO wiring, microphone placement, Home Assistant
topology, and provider accounts remain deployment-specific.
