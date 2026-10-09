#!/usr/bin/env bash
set -euo pipefail
[[ ${EUID} -eq 0 ]] || { printf 'Run this uninstaller as root.\n' >&2; exit 1; }
mapfile -t active < <(systemctl list-units --type=service --state=active,activating --no-legend 'dev-tools-*@*.service' | awk '{print $1}')
if [[ ${#active[@]} -gt 0 ]]; then systemctl stop "${active[@]}"; fi
rm -f /etc/systemd/system/dev-tools-*.service
if [[ $(readlink -f /usr/local/bin/dev-tools 2>/dev/null || true) == /opt/dev-tools/scripts/dev-tools ]]; then
  rm -f /usr/local/bin/dev-tools
fi
systemctl daemon-reload
printf 'Removed dev-tools entrypoint and workers. Declarations, caches and runtimes were preserved.\n'
