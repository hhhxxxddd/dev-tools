#!/usr/bin/env bash
set -euo pipefail

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
repo_root="$(cd -- "$(dirname -- "$script_path")/.." && pwd)"
profile_path="${HOME}/.profile"
start_marker="# >>> dev-tools >>>"
end_marker="# <<< dev-tools <<<"

command -v mise >/dev/null 2>&1 || {
  printf 'mise is not available on PATH.\n' >&2
  exit 1
}

python3 - "$profile_path" "$start_marker" "$end_marker" <<'PY'
from pathlib import Path
import re
import sys

if sys.version_info < (3, 11):
    raise SystemExit("dev-tools requires Python 3.11 or newer")

profile, start, end = sys.argv[1:]
path = Path(profile)
content = path.read_text(encoding="utf-8") if path.exists() else ""
pattern = rf"(?ms)^{re.escape(start)}.*?^{re.escape(end)}\n?"
content = re.sub(pattern, "", content).rstrip()
path.write_text(f"{content}\n" if content else "", encoding="utf-8", newline="\n")
PY

install -d /usr/local/bin
ln -sfn "$repo_root/scripts/dev-tools" /usr/local/bin/dev-tools
printf 'dev-tools installed: %s\n' /usr/local/bin/dev-tools
