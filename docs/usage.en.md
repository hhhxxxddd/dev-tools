# Configuration and operation

[中文](usage.md) · [Back to README](../README.en.md)

## Three configurations and native bindings

| File | Responsibility | Location |
|---|---|---|
| `config.toml` | Controller language, editor, information display, report and WSL distribution preferences | Each platform's user configuration directory |
| `dev-tools.toml` | Tasks, services, dependency graphs, build and sync policies | Project root; commit to the project |
| `mise.toml` / `.mise.toml` | Project runtime versions | Project root; commit to the project |
| Registration JSON | Source, native workspace, runtime user and storage identity | Each platform's native registry |

Preparation uses explicit versions from the target project's root mise files. Existing root files are preserved; when both filenames exist, their tool declarations are combined and equal-priority version conflicts are rejected. Controller preferences, user-global mise defaults, ancestor declarations and nested version declarations cannot replace root versions.
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
collectors = ["git", "mise", "winget", "store", "scoop", "apt", "snap", "rustup", "npm", "wsl"]
```

See the [annotated template](../config/settings.example.toml).

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
| mise | Read installed native versions; refresh uses outdated for updates allowed by current declarations, preserving pins, caches and release-age policy; also query the target distro when wsl is enabled |
| winget | Query available Windows application updates; skipped without refresh to avoid automatic source updates |
| store | Query updates for all installed Store apps using Microsoft's Store CLI, without --apply and with only a negative confirmation; retain results as partial if the CLI rejects redirected input; a missing CLI is recorded as missing, and unavailable target versions remain unknown |
| scoop | Read Windows application status; refresh updates Scoop/bucket indexes without updating installed apps |
| apt | List upgradable WSL packages; refresh updates APT indexes using noninteractive sudo when not root, recording permission failures |
| snap | Read installed WSL packages; refresh only queries version and revision updates using refresh --list, without installing |
| rustup | Query Windows/WSL toolchain and rustup updates, supporting native user Cargo directories without reading shell profiles; recorded separately from the mise installation inventory |
| npm | Read global packages and query updates for extra packages, excluding mise-managed packages, npm and corepack |
| wsl | Allow Windows to run enabled mise/APT/Snap/Rustup/npm collectors in the configured distro |

Report refreshes by default and may use the network or update local indexes. It never installs or upgrades software and accepts only built-in collectors.
`--no-refresh` disables Git fetch, index refresh and mise/Store/Snap/Rustup update queries, but npm update queries may still use the network; remove npm from collectors when those queries are unwanted. npm update queries are skipped when the mise-managed inventory cannot be determined. WSL skips Windows collectors.
mise, Store, Snap and Rustup update queries provide structured updates. Rustup's update exit code 100 is not a failure, and partial valid results are retained. Unknown response formats are marked parse-error rather than reported as fully up to date.

## Lifecycle and platform boundaries

The same Python worker handles dependencies, health, restart policies, monitoring and recovery on both platforms. Dependencies start and become ready first; stop reverses the graph. Failed startup rolls back newly started processes. Native process services without health probes have health unknown; Compose also checks container state and container-reported health.

Start automatically validates and executes the shared preparation plan: prepare native prerequisites and root runtimes, synchronize source, lockfiles and packages, prepare Compose images and Spring classpaths, then start services. While running, declaration, lockfile and configuration changes use the same flow, keeping the monitor alive through failures and retrying after corrections. Starting all services follows newly discovered services; start --service maintains only the selection and its dependencies. Explicit prepare remains available for previews or manual preparation. Stop publishes cancellation first, preventing services from restarting after an update.

Source builds keep services running. Resource/structural builds stop affected services and dependents, build and restore them. Branch/HEAD changes wait for Git operations and files to settle before running declared package refreshes and builds. Background preparation can supply runtimes explicitly declared at the project root and Spring DevTools JARs. Manual operations wait up to 15 seconds for the project lock; background operations yield when the project is busy.

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
