# Configuration and operation

[中文](usage.md) · [Back to README](../README.en.md)

## Environment selection

Every command defaults to the current platform. Select `-e win` / `-e wsl` explicitly; both sides use the same syntax. Place `-e` before or after the command; content after `--` remains ordinary command data.

```text
dev-tools list
dev-tools list --all
dev-tools list -e win
dev-tools config edit -e wsl
dev-tools sysinfo -e win --json
dev-tools report -e wsl --no-refresh --output report.json
dev-tools self status -e win --json
dev-tools upgrade -e win
```

Configuration, system information, reports and controller maintenance also work in both directions. Targets use their own configuration and editor; the caller's `--config` never crosses the boundary. Cross-platform path arguments (project directories, `--source` and report `--output`) resolve on the caller and are translated; already-native target absolute paths are retained, and report files need not exist yet. Initial Windows-to-WSL deployment still uses `self update -e wsl`.

Native WSL registration, lifecycle, deployment and recovery require `sudo`; Windows selects root automatically for WSL control. Windows operations launched from WSL retain Windows user privileges: Linux root is not a Windows administrator. `hosts` currently supports Windows only: use `hosts … -e win` from WSL. Selecting WSL fails explicitly without switching targets. Ordinary commands report missing or unreachable target deployments; queries never install them automatically.

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
| store | Discover candidates in bulk with Microsoft's Store CLI, then query each app by name; only explicitly confirmed updates enter updates, current apps are excluded, and unresolved candidates enter unverified; share the collector timeout, never use --apply and only provide negative confirmation; a missing CLI is recorded as missing and unavailable target versions remain unknown |
| scoop | Read Windows application status; refresh updates Scoop/bucket indexes without updating installed apps |
| apt | List upgradable WSL packages; refresh updates APT indexes using noninteractive sudo when not root, recording permission failures |
| snap | Read installed WSL packages; refresh only queries version and revision updates using refresh --list, without installing |
| rustup | Query Windows/WSL toolchain and rustup updates, supporting native user Cargo directories without reading shell profiles; recorded separately from the mise installation inventory |
| npm | Read global packages and query updates for extra packages, excluding mise-managed packages, npm and corepack |
| wsl | Allow Windows to run enabled mise/APT/Snap/Rustup/npm collectors in the configured distro |

Report refreshes by default and may use the network or update local indexes. It never installs or upgrades software and accepts only built-in collectors.
Explicit collection failures, timeouts, partial results, parse errors or repository discovery issues retain the report and exit 1. Missing optional tools, disabled entries or normal update findings do not themselves count as execution failures.
`--no-refresh` disables Git fetch, index refresh and mise/Store/Snap/Rustup update queries, but npm update queries may still use the network; remove npm from collectors when those queries are unwanted. npm update queries are skipped when the mise-managed inventory cannot be determined. WSL skips Windows collectors.
mise, Store, Snap and Rustup update queries provide structured updates. Rustup's update exit code 100 is not a failure, and partial valid results are retained. Unknown response formats are marked parse-error rather than reported as fully up to date.
Store's `discovery` retains the raw bulk output and candidates; `checks` retains each query's status, raw output and `update_available` verdict (true/false/null). Rejected bulk confirmation input, incomplete candidate counts, or failed/timed-out verification mark the collector partial; unverified apps are neither included in updates nor reported as current. Text reports show confirmation results rather than repeating bulk false positives.

## SSH

Running `dev-tools ssh` displays help, equivalent to `dev-tools ssh -h`; `-e win|wsl` selects the target's help.

```text
dev-tools ssh list
dev-tools ssh list --edit
dev-tools ssh list --edit -e wsl
dev-tools ssh list my-server
dev-tools ssh list -e win
dev-tools ssh list -e wsl --json
dev-tools ssh show my-server --json
dev-tools ssh edit
dev-tools ssh edit my-server -e win
dev-tools ssh connect my-server -e wsl
```

Omit `-e` to operate on the current platform. Windows reads the current user's `~/.ssh/config` and `%PROGRAMDATA%/ssh/ssh_config`; WSL reads the invoking user's `~/.ssh/config` and `/etc/ssh/ssh_config`, retaining the original user under sudo. Queries need no project registration or administrator rights.

The table shows alias, address, user, port, jump host and configuration status. The optional keyword is a case-insensitive substring filter on alias, address or user. Only concrete `Host` aliases are listed; wildcard rules supply defaults, and `known_hosts` is not used to infer servers.

User configuration precedes system configuration, with the first applicable value winning. Supports global and Host-scoped `Include`, lexically sorted glob matches, quoted paths, `~/`, `${HOME}`, existing environment variables, and static `%d` and `%u` paths. Relative Includes use the user's `.ssh` or system SSH configuration directory. Cycles, nesting beyond 16 levels, more than 128 files, files exceeding 1 MiB, non-UTF-8 files or invalid syntax retain readable results and exit 1.

This is a static configuration view. Complex `Match` conditions and host-dependent Include paths are explicitly identified; affected fields show “Needs evaluation.” Listing never runs `ssh -G`, `Match exec` or proxy commands, reads private keys, connects or writes files. Undeclared address, user and port default to the alias, invoking user and 22. `ProxyCommand` shows a marker without its command text. JSON includes source files and line numbers; incomplete reads set `complete=false`.

`show <ALIAS>` matches an exact alias case-insensitively and displays static address, user, port, proxy markers, declaration file and line. Conflicting case variants fail explicitly. Like list, it remains read-only and never runs native SSH or conditional commands.

`list --edit [query]` opens an interactive terminal list. Shortcut hints occupy one line above the input area; contextual guidance appears at the top. Use `i`/Enter to edit the selected field, Enter to stage it, Esc to cancel that edit, and Ctrl+U to clear input. `a` adds and `d`/Delete deletes. `s` or `:w` opens a diff: PgUp/PgDn scroll, `y` writes, and `n`/Esc returns to editing. `q`/`:q`/Esc exits and discards pending edits; Ctrl+C or end of input also exits and restores the terminal. An interactive terminal is required; `--json` is incompatible.

SSH fields are alias, HostName, User, Port and ProxyJump. The footer shows the selected field's source. Inherited fields display effective values; a blank edit means the local block has no declaration. Clearing an optional field removes its local declaration and restores inheritance. Automatic edits are limited to single-alias Host blocks inside the user's `~/.ssh`, in files without Match, with no Include/ProxyCommand or duplicate editable fields in the block. Other options, comments, BOM and newlines are preserved. Shared blocks, system configuration and complex rules stay read-only; use `ssh edit [alias]`. Ineffective changes under first-value precedence, changes affecting other aliases, and incomplete inventories are rejected. New entries append to the user config; files are created only on confirmed save.

Before saving, configuration and Include files are re-read and link destinations are checked; concurrent changes prevent overwrite. Original `.before` files, new `.draft` files and `files.json` are saved under native user configuration at `dev-tools/ssh/backups/<transaction>/`, independently of `--config`. Each file is replaced atomically with existing permissions retained. Failed multi-file saves attempt rollback and print the recovery directory for manual inspection. New WSL files use 0600, new directories 0700, and sudo retains invoking-user ownership. Diff previews hide command bodies; SSH, proxies and Match exec are never executed.

`edit [ALIAS]` uses the target's configured existing editor. Without an alias it opens that user's `~/.ssh/config`, creating it only on the first edit. New WSL files use 0600 and new directories 0700; existing permissions are preserved. An alias selects its actual declaration file, including Include files, retaining comments, line endings and rules without rewriting the configuration. Editors supporting line navigation, such as VS Code, Cursor, Vim and Nano, open the declaration line; other editors receive the file with its location printed for reference. For a missing alias, use edit without an alias first.

Save and close an interactive editor, then press Enter in the terminal to reread static configuration. Recognized issues produce warnings and exit 1 while retaining edits. This does not perform complete OpenSSH syntax validation or condition evaluation. User configuration normally needs no elevation; system and read-only Include files require existing write permission, without automatic elevation.

`connect <ALIAS_OR_DESTINATION>` invokes the target's native `ssh.exe` / `ssh`, including destinations absent from the static inventory. The native client handles terminal input/output, host-key confirmation, passwords, keys and agents, evaluating Match, proxies and connection behavior according to its configuration. Under WSL sudo, connections run as the invoking user and retain an available SSH_AUTH_SOCK. Keys and agents are not copied across platforms. A missing client prompts installation on the target; nothing is installed automatically.

`edit/connect` reject `--json`. Connect preserves native client/session exit codes, including 255 for connection failure and 130 for Ctrl+C; other commands retain the common CLI exit contract.

Cross-platform list/show results are rendered by the caller. Edit/connect execute on the target, with a read-only command-support check before connecting. Targets without the requested SSH operation receive an explicit update diagnostic, without automatic deployment or upgrades.

## Windows hosts

Running `dev-tools hosts` displays help, equivalent to `dev-tools hosts -h`, including on WSL.

```text
dev-tools hosts list
dev-tools hosts list --edit
dev-tools hosts list --edit -e win
dev-tools hosts list app.test --json
dev-tools hosts edit
dev-tools hosts edit dev
dev-tools hosts profiles
dev-tools hosts use dev
dev-tools hosts off
dev-tools hosts backups
dev-tools hosts restore
```

This module manages only Windows system hosts, locating it through the system API; it does not change WSL `/etc/hosts`. From WSL, explicitly add `-e win`. Profiles, backups, editors and permissions stay on Windows, without forwarding the caller's configuration file. The target must contain the hosts command.

`list [KEYWORD]` filters by IP, domain or comment. Columns are address, domains, comment, profile and state. Valid commented mappings are disabled; existing conflicting addresses for the same domain and IP family appear yellow. Existing invalid lines are retained and reported, with exit 1 for incomplete parsing. Configuration conflicts alone do not fail inventory reads. Listing entries, profiles or backups never creates files, elevates or flushes DNS.

`list --edit [query]` opens the same interactive list. Edit addresses, multiple domains and comments; use `a` to add, `d`/Delete to delete, and Space or editing the state column to toggle enabled state. Changes remain in memory until `s`/`:w` previews the diff and `y` saves; `q`/`:q`/Esc discards them. Unchanged lines, managed markers, encoding, BOM and newlines are retained. Saves reuse validation, concurrency checks, backups, UAC and DNS refresh. Editing the active managed block does not update its profile file; a later `use` reapplies the saved profile. Failed or interrupted saves preserve a draft and print its location after exiting. Successful saves display backup information and report any DNS refresh failure. `--edit` rejects `--json`; existing `edit [profile]` text editing remains available.

`edit` edits current hosts; `edit dev` edits the dev profile without activating it. Uses the configured editor, then VISUAL, EDITOR and finally Notepad. Edit a UTF-8 draft, save and close the editor, then press Enter in the terminal; Ctrl+C cancels. Interactive editing has no public `--json` mode; scripts can apply saved profiles with `use --json`. Validation failures, editor failures or concurrent changes preserve the draft and refuse overwrite.

Profiles use ordinary hosts syntax, for example:

```text
# Local development
127.0.0.1 app.test api.test
::1 app.test
```

Names contain 1–64 letters, digits, underscores or hyphens and exclude Windows device names. Profiles live under the Windows native user configuration directory in `dev-tools/hosts/profiles/<name>.hosts`, normally under `%LOCALAPPDATA%`; `--config` does not relocate them, and they do not belong to project configuration. Profiles require valid IPs and concrete domains; comments and multiple aliases are supported, wildcard domains are not. A domain may have separate IPv4 and IPv6 mappings; conflicting IPs within one family are rejected.

`use dev` validates the profile and conflicts with manual entries, then replaces the block between `# BEGIN dev-tools hosts: dev` and `# END dev-tools hosts`. Other entries and comments remain. Unrelated existing conflicts or invalid lines do not block switching; direct editing may preserve existing issues but cannot introduce invalid lines or conflicts. `off` removes only that block. `profiles` reports active, inactive, invalid or modified after activation; edits to a profile require another `use`. Damaged or duplicate managed markers block switching and can be repaired through `edit`.

System writes show a diff and save original bytes plus transaction checksums under `dev-tools/hosts/backups`. No differences means no write, backup or elevation. Only actual writes request UAC when needed. Original encoding, BOM, newlines outside the block and Windows security are retained; writes use file replacement and refresh the DNS cache. Files are limited to 10 MiB. Security software settings are never changed; cancelling UAC or detecting concurrent edits aborts the write.

`restore [BACKUP_ID]` selects the latest backup matching the current file's checksum by default, and creates another backup before restoration. Subsequent unrelated changes block full-file overwrite; inspect `backups` and merge manually. Backups are not deleted automatically. All operations except interactive `edit` and `list --edit` support `--json`, affecting only output; `use/off/restore --json` still modify hosts.

## Lifecycle and platform boundaries

WSL root and sudo environments can also use `-e win` to query, register and control Windows projects. When Linux PATH excludes Windows directories, PowerShell 7 is located from actual drive mounts; if needed, inbox Windows PowerShell reads Windows PATH and installation records without loading user profiles or changing system configuration. WSL root does not automatically obtain Windows administrator rights; hosts writes still request UAC when needed. Use `dev-tools hosts list -e win` (`hosts` is plural).

`dev-tools list` defaults to a read-only query of the current environment. Use `list -e win` / `list -e wsl` to select a target. Only `list --all` combines Windows and the configured WSL distribution. Identical registration names remain separate rows by environment. The caller renders both using its language settings, including declared ports and runtime types; queries do not update deployments or change running projects.
Columns are name, environment, ports, type and state. In interactive terminals, running/healthy is green, recovery pending/starting/partially running yellow, failed/unhealthy red, and stopped/unknown gray. Controller errors are red and notices yellow. Redirected output and `NO_COLOR` disable colors; JSON retains its original status identifiers and structure.
A summary list shows an unavailable environment in yellow and retains the other environment's results; JSON still returns `complete=false`, `errors` and exit code 1. `list -e wsl` / `list -e win` queries only the selected side. Each installation is updated separately; queries never install or update the other side. `self update -e wsl` can redeploy the current Windows source even when versions match.
`start` (including `--all`) and `restart` show stages and elapsed time for dependency analysis, preparation, synchronization, dependency/build tasks, service startup, health checks and monitor startup. Long steps show a waiting notice every 5 seconds. Progress goes to stderr; `--json` retains its JSON result and background maintenance emits no terminal progress. View original task output with `dev-tools logs my-app install --task`, replacing `install` with the actual task name.
`list --all --json` uses environment=all, retains each project's environment in projects, and includes complete and errors. If one query fails, the other environment's projects are retained, complete=false and the exit code is 1. Successful queries return complete=true and exit code 0. Remote queries have a 30-second timeout.

The same Python worker handles dependencies, health, restart policies, monitoring and recovery on both platforms. Dependencies start and become ready first; stop reverses the graph. Failed startup rolls back newly started processes. Native process services without health probes have health unknown; Compose also checks container state and container-reported health.

Running `dev-tools list -e wsl`, `dev-tools list --all` or `dev-tools status my-app -e wsl` from Windows also checks localhost access directly from Windows. IPv4 and IPv6 are probed separately so a timeout on one family cannot hide the other. Only running services with declared health ports are checked. Queries never start services, elevate, or change proxies, hosts, port forwarding or WSL configuration.

A local `health.http` supplies the HTTP/HTTPS path. TCP checks for Spring, Vite, Next, Nuxt, Astro and common Python web commands also receive an HTTP check. Windows HTTP access probes use GET to support GET-only health endpoints; general `net check` still uses HEAD. Package scripts are read statically from `package.json`, preferring the native workspace and falling back to source when the workspace is unreadable, without executing scripts or elevating; unrecognized TCP services retain TCP checks. Probes read response headers only, without following redirects or reading response bodies. HTTP 401/404 proves reachability; 5xx and timeouts fail. Probes run concurrently with bounded timeouts, deduplicate targets within a query and ignore HTTP proxy environment variables.

Failed forwarding appears as Windows access failed, with aggregate `ready/healthy=false`. Native WSL facts remain in `runtime_ready/runtime_healthy`; details are in `windows_access.checks`. Older WSL controllers without access metadata appear unchecked instead of implying healthy access. Queries inside WSL explicitly describe local-only health, which does not prove Windows forwarding works. Successful queries still exit 0; access failures are represented in status fields.

Start automatically validates and executes the shared preparation plan: prepare native prerequisites and root runtimes, synchronize source, lockfiles and packages, prepare Compose images and Spring classpaths, then start services. While running, declaration, lockfile and configuration changes use the same flow, keeping the monitor alive through failures and retrying after corrections. Starting all services follows newly discovered services; start --service maintains only the selection and its dependencies. Check inspects the plan read-only; prepare executes it manually. Stop publishes cancellation first, preventing services from restarting after an update.

```text
dev-tools check my-app
dev-tools check my-app --json
dev-tools prepare my-app
dev-tools prepare my-app --json
```

`check` shows declared versions, native prerequisites, preparation tasks and blockers without installing runtimes, synchronizing source or executing tasks. It shares the preparation plan with `prepare`, `start` and background maintenance; `scan` only discovers project declarations. `--json` controls output format only: `prepare --json` still performs preparation. Omit the project name in a directory that uniquely matches it.
Blocked scan/init/check results exit 1; argument errors exit 2 and interruptions exit 130. Successful status queries exit 0 and express readiness through the JSON ready field. See the [CLI and automation contract](cli-contract.en.md) for the full interface agreement.

Source builds keep services running. Resource/structural builds stop affected services and dependents, build and restore them. Branch/HEAD changes wait for Git operations and files to settle before running declared package refreshes and builds. Background preparation can supply runtimes explicitly declared at the project root and Spring DevTools JARs. Manual operations wait up to 15 seconds for the project lock; background operations yield when the project is busy.

| Content | Windows | WSL |
|---|---|---|
| Registrations | `%LOCALAPPDATA%/dev-tools/projects.d` | `/etc/dev-tools/projects.d` |
| State and logs | `%LOCALAPPDATA%/dev-tools/state` | `/var/lib/dev-tools` |
| Workspace | Original source directory | Independent directory under the runtime user's `~/.cache/dev-tools/build` |
| Supervision | Hidden processes with PID/start-time validation | Generic `dev-tools-worker@.service` template |
| Source sync | Use source directly | rsync excluding Git, packages and native build outputs |

### Log queries and retention

`dev-tools logs my-app` combines declared service and monitor logs, prefixing each line with `[service]`. `logs my-app api` selects api and preserves raw output. A single positional argument always names the project. Omit the project to infer it from the current directory; select a service with `logs --service api`. `--task` switches to preparation/build task logs, combining all declared tasks when no task is selected. The service positional argument cannot be combined with `--service`.

```powershell
dev-tools logs my-app
dev-tools logs my-app api --lines 200
dev-tools logs --service api
dev-tools logs my-app --task
dev-tools logs my-app -e wsl --follow
dev-tools logs my-app api -e win --follow
```

Use `-e win|wsl` from either side. The target reads its logs without copying them. The current environment is the default; matching names do not trigger automatic environment switching. Update both controller copies separately for the new argument contract. `--follow` watches all selected logs, waits for missing files, and exits 130 on Ctrl+C. Cross-platform following and preparation/start/stop/build operations do not use the ordinary query's 30-second timeout.

Queries do not start services, install dependencies or modify registrations/state. They read current log files only. `--lines` defaults to the last 100 lines per file, with each initial tail limited to 10 MiB; very long lines or large output can return fewer lines. Following reads each file fairly in bounded blocks and handles rotation, truncation and newly created files. UTF-8 and newline decoding state carries from the initial tail into new output, avoiding corrupted characters or duplicate newlines across writes.

Before a worker or task starts, an existing log of at least 10 MiB is rotated, retaining up to three archives named `.1`–`.3`. Oversized archives retain only their newest 10 MiB. Rotation happens at startup only: a single long-running process can grow its current log beyond 10 MiB, so the threshold is not a live size limit.

WSL prepare can supply Git, rsync, runtime-user tools and requested Docker/Compose prerequisites. Windows requires a configured, reachable native Docker engine.
Business services such as Redis/MySQL are declared by the project rather than inferred or installed automatically. This repository operates independently and does not read or migrate wsl-devctl registrations.
Rename preserves caches, state and Maven/Compose storage identities. A valid snapshot still allows stop/unregister when the current declaration is broken.
## Toolbox diagnostics

The following utilities run natively on Windows and WSL and support bidirectional `-e win|wsl` calls. The current platform is the default. They read target-native preferences and data without requiring a registered project, installing dependencies or requesting elevation. Running any toolbox command (`ssh`, `hosts`, `ports`, `which`, `net`) alone displays its help, equivalent to `-h`; required arguments for specific operations are still validated.

### Port ownership

```text
dev-tools ports list
dev-tools ports list --protocol tcp -e wsl
dev-tools ports show 3000 -e win --json
```

Inspect actual TCP listeners and bound UDP sockets: IPv4/IPv6, address, port, PID, process name, readable executable path and project ownership. Windows uses native APIs; WSL reads `/proc`. Collectors filter ports and protocols before reading related process details. Declared project ports are not treated as observed listeners.

Windows requests the system TCP listener table directly, excluding established connections. Listener rows with a zero state field are retained so actual listening ports are not omitted on affected systems.

Windows project attribution cross-checks active worker PIDs, process birth times and the current process tree. WSL uses live systemd service cgroup identities to remain independent of wall-clock adjustments. Ports and process names alone never establish ownership. Compose forwarding and unverified ownership remain unattributed. Permissions or processes exiting during a snapshot may leave PIDs null; results are retained with warnings and exit 1. No matching ports returns an empty inventory and exit 0. Processes are never terminated.

### Command paths

```text
dev-tools which python
dev-tools which node -e wsl --json
```

List candidates in the target process's PATH order, also respecting PATHEXT extension order on Windows. The first match is selected; duplicate paths are removed and statically resolvable links show their targets. Sources identify native mise, Scoop and virtual-environment directories; other candidates use PATH provenance. Windows application execution aliases have a null target when it cannot be resolved statically.

Commands, shims, shell profiles and project code are never executed. Shell aliases, functions and dynamic shim targets are not evaluated, and the controller Python binding is not interpreted as a project runtime. This is a PATH inventory, which can differ from an interactive shell or a project's mise environment. A complete match exits 0; missing commands or partial reads exit 1.

### Network connections

```text
dev-tools net info -e win
dev-tools net info -e wsl --json
dev-tools net check localhost:3000 -e win
dev-tools net check https://example.com/health -e wsl --timeout 5
dev-tools net check [::1]:3000 --json
```

`info` reports target-native DNS servers and environment proxies with credentials hidden. Windows also reports selected networking declarations in `.wslconfig`; WSL reports its effective networking mode when native `wslinfo` is available. Declared and effective settings remain distinct; unavailable observations stay unknown. Proxy information comes only from environment variables and does not represent every application's or Windows system proxy's effective configuration.

`check` connects directly: host/port targets probe DNS/TCP; HTTP URLs continue with HEAD; HTTPS URLs first verify TLS and certificates. HTTP 400 or higher fails. Redirects are not followed, bodies are not read and GET is never substituted. URL query parameters are sent but not echoed; embedded user credentials are rejected. Displayed environment proxies are diagnostic context and are not used for the connection.

`--timeout` bounds each stage to 1–60 seconds, default 5. Total time may span several stages. DNS runs in an isolated standard-library child process, HTTP has a total stage deadline, and subsequent stages are skipped after failure. Success exits 0, any failed stage exits 1 and invalid arguments exit 2. DNS, proxies, hosts, firewall and WSL configuration are never modified.
