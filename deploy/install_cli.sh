#!/usr/bin/env bash
# install_cli.sh — install the `aidog` shell command into PATH (idempotent).
#
# The product's only shell entry point is `aidog` (repo root dispatcher ->
# deploy/aidog_persist.py). Short, memorable, never `imaidog`.
#
# Usage:  bash deploy/install_cli.sh
# Where:  copies a self-locating wrapper to the first writable bin dir so the
#         command keeps working after the project folder is moved to another
#         machine (re-run this script there — it is idempotent).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Candidate bin dirs, in preference order (writable, on common PATHs).
CANDIDATES=(
  "$HOME/.local/bin"
  "/usr/local/bin"
)

TARGET=""
for d in "${CANDIDATES[@]}"; do
  if [ -d "$d" ] && [ -w "$d" ]; then
    TARGET="$d/aidog"
    break
  fi
done

if [ -z "$TARGET" ]; then
  echo "install_cli: no writable bin directory found (tried: ${CANDIDATES[*]})" >&2
  exit 1
fi

# Self-locating wrapper: the project root is resolved at every run via the
# script location recorded below, so moving the project only needs a re-install
# if the wrapper itself moved. We record the live project path in the wrapper.
cat > "$TARGET" <<EOF
#!/usr/bin/env bash
# \`aidog\` — I'm AiDog CLI (installed by deploy/install_cli.sh).
exec python3 "$ROOT/deploy/aidog_persist.py" "\$@"
EOF
chmod 0755 "$TARGET"

echo "install_cli: installed -> $TARGET"
echo "verify:"
if command -v aidog >/dev/null 2>&1; then
  aidog list >/dev/null 2>&1 && echo "  aidog: OK (aidog list ran)" || echo "  aidog: installed but 'aidog list' failed (check python3)"
else
  echo "  not on PATH yet — open a new shell or add $(dirname "$TARGET") to PATH"
fi
