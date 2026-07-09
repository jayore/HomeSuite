#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${HOMESUITE_DIR:-$HOME/homesuite}"
SHORTCUT_DIR="${HOMESUITE_SHORTCUT_DIR:-$HOME/.local/bin}"

mkdir -p "$SHORTCUT_DIR"

write_shortcut() {
  local name="$1"
  local body="$2"
  local path="$SHORTCUT_DIR/$name"
  printf '%s\n' "$body" > "$path"
  chmod 0755 "$path"
}

python_exec='exec "$HOMESUITE_DIR/.venv/bin/python"'

write_shortcut "homesuite-doctor" '#!/usr/bin/env bash
set -euo pipefail
HOMESUITE_DIR="'"$INSTALL_DIR"'"
'"${python_exec}"' "$HOMESUITE_DIR/tools/doctor.py" "$@"'

write_shortcut "homesuite-test" '#!/usr/bin/env bash
set -euo pipefail
HOMESUITE_DIR="'"$INSTALL_DIR"'"
export PIPHONE_NO_RUNTIME_INIT=1
export PIPHONE_LIGHT_IMPORT=1
export PIPHONE_SKIP_PID_LOCK=1
export PIPHONE_TEST_MODE=1
'"${python_exec}"' "$HOMESUITE_DIR/tools/test_commands.py" "$@"'

write_shortcut "homesuite-live" '#!/usr/bin/env bash
set -euo pipefail
HOMESUITE_DIR="'"$INSTALL_DIR"'"
'"${python_exec}"' "$HOMESUITE_DIR/tools/test_commands.py" "$@" --live'

write_shortcut "homesuite-chat" '#!/usr/bin/env bash
set -euo pipefail
HOMESUITE_DIR="'"$INSTALL_DIR"'"
'"${python_exec}"' "$HOMESUITE_DIR/ppchat.py" --live "$@"'

write_shortcut "homesuite-chattest" '#!/usr/bin/env bash
set -euo pipefail
HOMESUITE_DIR="'"$INSTALL_DIR"'"
export PIPHONE_NO_RUNTIME_INIT=1
export PIPHONE_LIGHT_IMPORT=1
export PIPHONE_SKIP_PID_LOCK=1
export PIPHONE_TEST_MODE=1
'"${python_exec}"' "$HOMESUITE_DIR/ppchat.py" "$@"'

write_shortcut "homesuite-youtube-pair" '#!/usr/bin/env bash
set -euo pipefail
HOMESUITE_DIR="'"$INSTALL_DIR"'"
'"${python_exec}"' "$HOMESUITE_DIR/tools/youtube_pair.py" "$@"'

write_shortcut "homesuite-youtube-oauth" '#!/usr/bin/env bash
set -euo pipefail
HOMESUITE_DIR="'"$INSTALL_DIR"'"
'"${python_exec}"' "$HOMESUITE_DIR/tools/youtube_oauth.py" "$@"'

# Short legacy-style aliases. These are intentionally kept because the original
# HomeSuite/PiPhone development flow used them heavily, and they are pleasant to
# type during setup and debugging.
write_shortcut "ppdoctor" '#!/usr/bin/env bash
exec homesuite-doctor "$@"'

write_shortcut "pptest" '#!/usr/bin/env bash
set -euo pipefail
HOMESUITE_DIR="'"$INSTALL_DIR"'"
export PIPHONE_NO_RUNTIME_INIT=1
export PIPHONE_LIGHT_IMPORT=1
export PIPHONE_SKIP_PID_LOCK=1
export PIPHONE_TEST_MODE=1
if [[ "$#" -eq 0 ]]; then
  '"${python_exec}"' "$HOMESUITE_DIR/command_repl.py"
else
  '"${python_exec}"' "$HOMESUITE_DIR/tools/test_commands.py" "$@" --capture
fi'

write_shortcut "pplive" '#!/usr/bin/env bash
set -euo pipefail
HOMESUITE_DIR="'"$INSTALL_DIR"'"
if [[ "$#" -eq 0 ]]; then
  '"${python_exec}"' "$HOMESUITE_DIR/command_repl.py" --live
else
  '"${python_exec}"' "$HOMESUITE_DIR/tools/test_commands.py" "$@" --live
fi'

write_shortcut "ppchat" '#!/usr/bin/env bash
exec homesuite-chat "$@"'

write_shortcut "ppchattest" '#!/usr/bin/env bash
exec homesuite-chattest "$@"'

cat <<EOF
Installed HomeSuite shortcuts in $SHORTCUT_DIR:
  homesuite-doctor, homesuite-test, homesuite-live, homesuite-chat, homesuite-chattest
  homesuite-youtube-pair, homesuite-youtube-oauth
  ppdoctor, pptest, pplive, ppchat, ppchattest

If these commands are not found in the current shell, open a new shell or add
$SHORTCUT_DIR to PATH.
EOF
