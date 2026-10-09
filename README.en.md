# dev-tools

[中文](README.md)

Discover projects, prepare development environments and control services with the same commands on Windows and WSL.
Projects share version and workflow declarations; runtimes, packages, caches and process supervision remain native to each platform.

Version **0.4.0**; scan and project commands use JSON schema **3**, and `dev-tools.toml` uses schema **1**.

## Installation

### Install both Windows and WSL entrypoints

You need PowerShell 7, Git and an existing WSL distribution. WSL service control requires a running systemd.
An existing Windows mise installation is used directly; otherwise, Scoop must already be available.
Automatic mise installation in WSL uses Debian/Ubuntu extrepo/APT sources.

```powershell
git clone https://github.com/hhhxxxddd/dev-tools.git
cd dev-tools
.\scripts\bootstrap.ps1
. $PROFILE
dev-tools help
```

Bootstrap defaults to Ubuntu; use `-Distro <distribution>` for another distro. It verifies the checkout's Git origin before installing missing mise and deploying the host and entrypoints. It does not install WSL or Scoop. The runtime target still comes from `[wsl].distro` in your preferences; update that setting when using another distribution.

### Install each entrypoint separately

On Windows with mise already installed:

```powershell
.\scripts\install.ps1
. $PROFILE
```

In WSL with mise, rsync and systemd already available:

```bash
sudo bash scripts/install.sh
dev-tools help
```

The controller runs on Python **3.14.8**, independently of any project's Python. The Windows entrypoint references the current checkout. WSL copies the program to `/opt/dev-tools`, keeps its host in `/opt/dev-tools/host-mise`, and installs `/usr/local/bin/dev-tools`. Installers leave user-global mise tool declarations alone and do not register or start projects.

To update, run `git pull --ff-only` in the source checkout and rerun the corresponding installer. Rerunning bootstrap updates both deployments.
Uninstall with `scripts/uninstall.ps1` or `sudo bash scripts/uninstall.sh`. WSL uninstallation stops active workers and removes the entrypoint and systemd templates. Project declarations, registrations, state, caches and runtimes are preserved.

## Start with a project

Run in the project's source directory:

```text
dev-tools scan
dev-tools init --dry-run
dev-tools init
dev-tools register
dev-tools prepare --dry-run --json
dev-tools prepare
dev-tools start
dev-tools status
dev-tools list
```

1. `scan` parses version declarations from known metadata. `init` creates missing dev-tools.toml and root mise declarations. Both parse project metadata without running project code, package scripts, wrappers or downloaded content; the only writes during init create missing declarations.
2. Review generated versions, startup arguments, ports and health checks. Existing files are preserved. Unresolved versions, equal-priority conflicts and invalid metadata include their source locations; address those diagnostics first.
3. `register` creates a native binding. `prepare --dry-run` validates the same plan used for execution. Only explicit prepare installs project runtimes and prepares packages and framework artifacts.
4. `start` launches prepared services and monitors. When declarations, lockfiles or relevant build metadata change, follow the diagnostics and prepare again. Startup never fills in missing runtimes.

Discovery supports Node package workspaces, Maven/Spring Boot, Python/uv and existing Compose declarations. Scanning also reads version metadata such as Gradle files, without promising generated workflows for every detected build system. Edit declarations for complex projects, or consult the [project reference](docs/project-config.en.md) and [examples](examples/README.md).

`init --runtime auto|host|compose` chooses workflow discovery; the default auto favors existing Compose. `init --toolchain system` uses installed system tools. The default mise selects runtimes from root project declarations.

### Choose Windows or WSL

Project commands accept `-e win|wsl`, also spelled `--env`. Omission selects the current platform. Place the environment option before or after the command:

```text
dev-tools -e wsl register .
dev-tools prepare my-app -e wsl --dry-run
dev-tools -e wsl prepare my-app
dev-tools -e wsl start my-app
dev-tools -e win list
```

Cross-platform calls translate paths and preserve the caller's working directory. CLI values use win; shared configuration overlays and JSON platform identifiers use windows.

Native WSL register, prepare, start, stop, restart, sync, build, rename and unregister require root: use `sudo dev-tools ...`. Prepare previews and read-only operations do not require root. Windows forwarding runs WSL control as root and registers projects for the distro's default user unless `register --user` selects another user. Native Windows operations use the current user.

PATH defaults to the current directory for scan/init/register. Other project commands may omit NAME only when the current directory uniquely matches a registration's source or native workspace, including descendants, in the selected platform. Zero or multiple matches require an explicit name.

## Commands

Project commands live directly at the top level. `dev-tools` or `dev-tools help` lists all commands. Use `dev-tools help <command>` or `dev-tools <command> --help` for its arguments.

| Command | Purpose and main options |
|---|---|
| `scan [PATH]` | Statically identify tool versions and diagnostics; `--json` |
| `init [PATH]` | Create missing declarations; `--dry-run`, `--name`, `--runtime`, `--toolchain`, `--json` |
| `register [PATH]` | Create a native binding; `--name`, `--user` (WSL), `--force`, `--json` |
| `list` | List projects and state for the selected platform; `--json` |
| `show [NAME]` | Show the declaration and native binding; `--json` |
| `prepare [NAME]` | Install and prepare the project; `--dry-run`; `--json` requires `--dry-run` |
| `start [NAME]` | Start services and monitors; `--service` includes its dependencies; `--json` |
| `stop [NAME]` | Stop all project services and monitors; `--json` |
| `restart [NAME]` | Restart project services and monitors; `--json` |
| `status [NAME]` | Show preparation fingerprints, services, health and recovery; `--json` |
| `logs [NAME] SERVICE` | Service or `__sync`/`__watch` logs; `--task`, `--lines`, `--follow` |
| `sync [NAME]` | Manually synchronize source to the native workspace; `--json` |
| `build [NAME]` | Build with prepared runtimes; `--service`, `--kind branch\|source\|resource\|structural`, `--json` |
| `rename [NAME] NEW_NAME` | Change the native alias and restore previously active services; `--json` |
| `unregister [NAME]` | Stop services and monitors, then remove the registration; `--purge` (WSL only), `--json` |
| `config [edit\|check]` | Show, edit or validate controller preferences; `--json` for showing and checking |
| `sysinfo` | Read current machine information, configured directories and PATH tools; `--json` |
| `report` | Gather Git and software information; `--refresh`/`--no-refresh`, `--timeout`, `--output`, `--json` |
| `help [COMMAND]` | Show all commands or help for one command |

All commands accept `--config FILE`; `dev-tools --version` shows the program version. Config/sysinfo/report run on the current platform and reject the project-only -e option.

Logs always requires a service or task name; rename always requires the new alias. `register --force` allows another binding for the same source and runtime user without overwriting conflicting bindings.
Unregister preserves source, state, caches and runtimes by default. WSL `--purge` only deletes a validated native workspace. Windows does not support purging source.

## Three configurations and native bindings

| File | Responsibility | Location |
|---|---|---|
| `config.toml` | Controller language, editor, information display, report and WSL distribution preferences | Each platform's user configuration directory |
| `dev-tools.toml` | Tasks, services, dependency graphs, build and sync policies | Project root; commit to the project |
| `mise.toml` / `.mise.toml` | Project runtime versions | Project root; commit to the project |
| Registration JSON | Source, native workspace, runtime user and storage identity | Each platform's native registry |

Preparation uses explicit versions from the target project's root mise files. Existing root files are preserved; equal-priority conflicts between the two filenames are rejected. Controller preferences, user-global mise defaults, ancestor declarations and nested version declarations cannot replace root versions.
Shared declarations exclude machine source paths, usernames and cache roots. Windows and WSL may read the same source declaration while keeping executables, virtual environments, packages and build outputs separate.

### Controller preferences

```text
dev-tools config
dev-tools config edit
dev-tools config check
dev-tools config --json
```

The default file is `%LOCALAPPDATA%/dev-tools/config.toml` on Windows. WSL uses `$XDG_CONFIG_HOME/dev-tools/config.toml`, or `~/.config/dev-tools/config.toml` when XDG is unset. Under sudo, WSL uses the invoking user's preferences and runs the editor as that user.

File precedence is `--config FILE` > `DEV_TOOLS_CONFIG` > native default path. Place --config before or after the command. Missing files use defaults; only config edit creates a template. Invalid files fail clearly while help and config showing/editing/checking remain available. Unknown fields are rejected. Relative configured directories resolve against the file's parent.

```toml
language = "zh"
editor = []

[wsl]
distro = "Ubuntu"

[sysinfo]
sections = ["system", "directories", "tools"]
show_missing = true
# Omit tools for native defaults; an explicit list replaces them, [] disables them.
# tools = [{ command = "git", description = { zh = "版本控制", en = "Version control" } }]
directories = []

[report]
roots = []
max_depth = 2
timeout = 90
refresh = true
collectors = ["git", "mise", "winget", "scoop", "apt", "npm", "wsl"]
```

See the [annotated template](config/settings.example.toml).

- **Language:** Chinese zh is the default; set en for English on the next invocation. There is no language flag or public language environment variable. Help, notices and controller diagnostics follow the setting. Third-party output, user text, JSON keys and status identifiers keep their original values. Cross-platform commands carry the resolved caller language while settings files remain separate.
- **Editor:** a nonempty editor argv array > VISUAL > EDITOR > Notepad on Windows / vi in WSL. For example, `editor = ["code", "--wait"]`; the editor must already be installed. The file is validated when the editor returns. GUI launchers may return early; run config check after saving if needed.
- **Distribution:** `[wsl].distro` selects the same distro for project forwarding and Windows reports. `DEV_TOOLS_DISTRO` temporarily overrides it. Installer-generated PowerShell functions do not pin a distro.
- **Sysinfo:** sections chooses displayed groups; tools supplies PATH probes, descriptions and ordering; directories lists native paths and optional entrypoint names. Descriptions can be strings or `{ zh = "...", en = "..." }`. `show_missing = false` hides missing items. Disabled groups perform no probes. Tools are never executed and PowerShell profiles are not read.
- **Report:** roots chooses Git repository discovery scope; the default [] scans no repositories. max_depth is 0–5, and per-command timeout is 1–600 seconds, defaulting to 90. An explicit `collectors = []` executes no collectors. CLI timeout and refresh options override this invocation only.

### Report collection and refresh

```text
dev-tools sysinfo --json
dev-tools report --no-refresh
dev-tools report --json --output report.json
```

| Collector | Behavior |
|---|---|
| git | Discover repositories in roots and inspect worktree, branch and remote differences; refresh fetches refs without pulling |
| mise | Read installed native tool versions; also query the target distro when wsl is enabled |
| winget | Query available Windows application updates; skipped without refresh to avoid automatic source updates |
| scoop | Read Windows application status; refresh updates Scoop/bucket indexes without updating installed apps |
| apt | List upgradable WSL packages; refresh updates APT indexes using noninteractive sudo when not root, recording permission failures |
| npm | Read global packages and query updates for extra packages, excluding mise-managed packages, npm and corepack |
| wsl | Allow Windows to run enabled mise/APT/npm collectors in the configured distro |

Report refreshes by default and may use the network or update local indexes. It never installs or upgrades software and accepts only built-in collectors.
`--no-refresh` disables Git fetch and index refresh, but npm update queries may still use the network; remove npm from collectors when those queries are unwanted. npm update queries are skipped when the mise-managed inventory cannot be determined. WSL skips Windows collectors.

## Lifecycle and platform boundaries

The same Python worker handles dependencies, health, restart policies, monitoring and recovery on both platforms. Dependencies start and become ready first; stop reverses the graph. Failed startup rolls back newly started processes. Native process services without health probes have health unknown; Compose also checks container state and container-reported health.

After validating its plan, prepare stops previously active workers, prepares native prerequisites and root runtimes, synchronizes source, runs tasks, prepares Compose images and Spring classpaths, then restores the active set. Failures save progress and recovery records. Prepare again after fixing the cause. Explicit stop cancels pending restoration intent.

Source builds keep services running. Resource/structural builds stop affected services and dependents, build and restore them. Branch/HEAD changes wait for Git operations and files to settle before running declared package refreshes and builds. Background workers never install runtimes or download Spring DevTools JARs. Manual operations wait up to 15 seconds for the project lock; background operations yield when the project is busy.

| Content | Windows | WSL |
|---|---|---|
| Registrations | `%LOCALAPPDATA%/dev-tools/projects.d` | `/etc/dev-tools/projects.d` |
| State and logs | `%LOCALAPPDATA%/dev-tools/state` | `/var/lib/dev-tools` |
| Workspace | Original source directory | Independent directory under the runtime user's `~/.cache/dev-tools/build` |
| Supervision | Hidden processes with PID/start-time validation | Generic `dev-tools-worker@.service` template |
| Source sync | Use source directly | rsync excluding Git, packages and native build outputs |

WSL prepare can supply Git, rsync, runtime-user tools and requested Docker/Compose prerequisites. Windows requires a configured, reachable native Docker engine.
Business services such as Redis/MySQL are declared by the project rather than inferred or installed automatically. This repository operates independently and does not read or migrate wsl-devctl registrations.
Rename preserves caches, state and Maven/Compose storage identities. A valid snapshot still allows stop/unregister when the current declaration is broken.

## Structure and verification

```text
src/dev_tools/
  cli.py, command_help.py                  Command definitions and localized help
  settings.py, i18n.py, locales/           Controller preferences and translations
  scanner.py, metadata.py, versions.py     Static metadata and version discovery
  project_models.py, workflows.py          Scan models, initialization and mise isolation
  projects/                               Declarations, plans, execution, drivers, lifecycle and workers
  runtimes/router.py                      Cross-environment command and path transport
  runtimes/platforms/                     Native storage, users, sync, processes and systemd
scripts/                                  Installation, entrypoints and process adapters
systemd/                                  Generic worker template
```

Python has no third-party runtime dependencies and supports `>=3.14.8,<3.15`. Use native Python and add src to PYTHONPATH for development checks.

Windows:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m unittest discover -v tests
uvx ruff check src tests
uvx ruff format --check src tests
```

WSL:

```bash
export PYTHONPATH="$PWD/src"
python3 -m unittest discover -v tests
uvx ruff check src tests
uvx ruff format --check src tests
```

Entrypoint changes also require checking `scripts/dev-tools.ps1` and `scripts/dev-tools`. Parse every PowerShell installer before testing bootstrap changes. Maintenance boundaries are in [AGENTS.md](AGENTS.md).

Real verification scripts use temporary registrations, state and caches: `tests/integration/lifecycle_smoke.py` checks HTTP services, hot builds, recovery and rename; `toolchain_smoke.py` uses installed Java/Node/Python/Maven; `transport_smoke.py` checks bidirectional paths, implicit names, help, preferences and language from Windows. WSL lifecycle tests require root and systemd. Toolchain tests add no runtime versions; transport tests require both entrypoints installed.

[MIT License](LICENSE)
