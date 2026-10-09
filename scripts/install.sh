#!/usr/bin/env bash
set -euo pipefail

[[ ${EUID} -eq 0 ]] || { printf 'Run this installer as root.\n' >&2; exit 1; }
command -v mise >/dev/null 2>&1 || { printf 'Install mise first.\n' >&2; exit 1; }
repo_root=$(cd -- "$(dirname -- "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd -P)
install_root=$(readlink -m "${DEV_TOOLS_INSTALL_ROOT:-/opt/dev-tools}")
command_root=${DEV_TOOLS_COMMAND_ROOT:-/usr/local/bin}
unit_root=${DEV_TOOLS_UNIT_DIR:-/etc/systemd/system}
config_root=${DEV_TOOLS_CONFIG_ROOT:-/etc/dev-tools}
state_root=${DEV_TOOLS_STATE_ROOT:-/var/lib/dev-tools}
[[ "$install_root" == /* && "$install_root" != / && "$install_root" != "$repo_root" && "$repo_root" != "$install_root/"* && "$install_root" != "$repo_root/"* ]] || {
  printf 'Installation root must be absolute and separate from the checkout.\n' >&2; exit 1;
}

for target in "$install_root" "$install_root/src" "$install_root/src/dev_tools" "$install_root/scripts" "$install_root/config" "$install_root/docs" "$install_root/examples" "$install_root/host-mise" "$install_root/.host-python" "$install_root/.release.json" "$install_root/README.md" "$install_root/README.en.md" "$install_root/CHANGELOG.md" "$install_root/AGENTS.md" "$install_root/LICENSE"; do
  [[ ! -L "$target" ]] || { printf 'Refusing symbolic-link installation target: %s\n' "$target" >&2; exit 1; }
done
if [[ -f "$install_root/src/dev_tools/__init__.py" ]]; then
  active_units=$(systemctl list-units 'dev-tools-worker@*.service' --state=active,activating,deactivating --no-legend --plain)
  [[ -z "$active_units" ]] || {
    existing_host=$(cat "$install_root/.host-python" 2>/dev/null || true)
    if [[ -x "$existing_host" ]]; then
      PYTHONPATH="$repo_root/src" "$existing_host" -P -c 'import os; from dev_tools.i18n import language_scope, t; from dev_tools.settings import load_settings; lang = os.environ.get("DEV_TOOLS_TRANSPORT_LANGUAGE") or load_settings(tolerate_invalid=True).language; scope = language_scope(lang); scope.__enter__(); print(t("Stop WSL projects before updating the controller: dev-tools -e wsl stop <project>"))' >&2
    else
      printf 'Stop WSL projects before updating the controller: dev-tools -e wsl stop <project>\n' >&2
    fi
    printf '%s\n' "$active_units" >&2
    exit 1
  }
fi
install -d -m 0755 "$install_root"
export MISE_DATA_DIR="$install_root/host-mise"
mise --no-config --yes install python@3.14.8
python_root=$(mise --no-config where python@3.14.8)
printf '%s\n' "$python_root/bin/python3" > "$install_root/.host-python"
chmod 0644 "$install_root/.host-python"
"$python_root/bin/python3" -c 'import sys; raise SystemExit(sys.version_info < (3, 14, 8))'
install -d -m 0755 "$install_root/src" "$install_root/scripts" "$install_root/config" "$install_root/docs" "$install_root/examples"
for target in "$install_root/src" "$install_root/scripts" "$install_root/config" "$install_root/docs" "$install_root/examples"; do
  [[ $(readlink -f "$target") == "$target" ]] || { printf 'Unexpected installation target: %s\n' "$target" >&2; exit 1; }
done
# The controller stays available when its Windows checkout is unavailable.
# Copy only owned source; existing project registrations and caches are preserved.
command -v rsync >/dev/null 2>&1 || { printf 'Install rsync before deploying dev-tools.\n' >&2; exit 1; }
rsync -a --delete --exclude '__pycache__' "$repo_root/src/dev_tools/" "$install_root/src/dev_tools/"
rsync -a --delete "$repo_root/scripts/" "$install_root/scripts/"
rsync -a --delete --exclude "*.local.*" "$repo_root/config/" "$install_root/config/"
rsync -a --delete "$repo_root/docs/" "$install_root/docs/"
rsync -a --delete "$repo_root/examples/" "$install_root/examples/"
install -m 0644 "$repo_root/README.md" "$install_root/README.md"
install -m 0644 "$repo_root/README.en.md" "$install_root/README.en.md"
install -m 0644 "$repo_root/CHANGELOG.md" "$install_root/CHANGELOG.md"
install -m 0644 "$repo_root/AGENTS.md" "$install_root/AGENTS.md"
install -m 0644 "$repo_root/LICENSE" "$install_root/LICENSE"
if [[ -f "$repo_root/.release.json" ]]; then
  install -m 0644 "$repo_root/.release.json" "$install_root/.release.json"
else
  rm -f -- "$install_root/.release.json"
fi
chmod 0755 "$install_root/scripts/dev-tools"
install -d "$command_root" "$unit_root" "$config_root/projects.d" "$config_root/examples" "$state_root"
ln -sfn "$install_root/scripts/dev-tools" "$command_root/dev-tools"
install -m 0644 "$repo_root/systemd/"dev-tools-*.service "$unit_root/"
install -m 0644 "$repo_root/examples/"*.toml "$config_root/examples/"
printf '# dev-tools\n\n中文指南：%s/README.md\n\nEnglish guide: %s/README.en.md\n\n项目模板 / Project templates: %s/examples\n' "$install_root" "$install_root" "$config_root" > "$config_root/README.md"
systemctl daemon-reload
printf 'Installed dev-tools. No projects were registered, started or migrated.\n'
