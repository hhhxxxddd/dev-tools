#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
repo_root="$(cd -- "$(dirname -- "$script_path")/.." && pwd)"
profile_path="${HOME}/.profile"
start_marker="# >>> dev-tools >>>"
end_marker="# <<< dev-tools <<<"

python3 - "$profile_path" "$start_marker" "$end_marker" <<'PY'
from pathlib import Path
import re
import sys

profile, start, end = sys.argv[1:]
path = Path(profile)
if path.exists():
    content = path.read_text(encoding="utf-8")
    pattern = rf"(?ms)^{re.escape(start)}.*?^{re.escape(end)}\n?"
    path.write_text(re.sub(pattern, "", content), encoding="utf-8", newline="\n")
PY

target="$(readlink -f /usr/local/bin/dev-tools 2>/dev/null || true)"
if [[ "$target" == "$repo_root/scripts/dev-tools" ]]; then
  rm -f /usr/local/bin/dev-tools
fi
printf 'dev-tools WSL entrypoint removed. Runtime installations were preserved.\n'
