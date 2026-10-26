# Project configuration reference

[中文](project-config.md) · [Back to README](../README.en.md)

`dev-tools.toml` is the shared declaration in the project root, describing how to prepare and run the project.
Runtime versions belong in the same root's `mise.toml` or `.mise.toml`; manage controller preferences with `dev-tools config`.
Register stores native source paths, users, caches and storage identities outside the shared declaration.

## Minimal example

For an npm project with package-lock.json and a dev script. Declare the Node version in the root mise file.

```toml
schema = 1
name = "my-app"
toolchain = "mise"
rebuild_on_branch = true

[tasks.install]
command = ["npm", "ci"]
manager = "npm"
tools = ["node"]

[services.web]
command = ["npm", "run", "dev", "--", "--port", "5173"]
prepare = ["install"]
tools = ["node"]
restart = "on-failure"

[services.web.health]
tcp = 5173
timeout = 30
```

On Windows, run `dev-tools register` and `dev-tools check --json`, review the plan, then start with automatic preparation.
Use sudo for native WSL mutations; previews do not require sudo.

## Root fields

| Field | Default or requirement | Meaning |
|---|---|---|
| schema | Integer 1, required | Declaration format version |
| name | Required | Default registration name |
| toolchain | mise | mise uses explicit root versions; system uses existing system tools |
| dependency_mode | auto | Synchronize known package-manager locks; locked prevents automatic lock changes |
| discovery | manual; init generates auto | Keep discovering modules while preserving custom values, or use only manual declarations |
| scan | Empty table | Project metadata discovery scope; see below |
| rebuild_on_branch | true | Run preparation tasks and structural builds after branch/HEAD transitions settle |
| sync_exclude | [] | WSL sync exclusion patterns that preserve custom native outputs |
| sync_include | [] | WSL inclusion patterns evaluated before exclusions, for source files that must be synced |
| tasks | Empty table | Named tasks |
| services | Empty table | Named services |
| builds | Empty table | Build monitoring policies named after services |

Project, task and service names contain 1–63 characters, start with a lowercase letter or digit, and otherwise allow lowercase letters, digits, dots, underscores and hyphens. Windows reserved device names are rejected.
Unknown fields, missing dependencies and dependency cycles are rejected.

WSL excludes build, dist, target and dependency directories by default. If a project uses build for source files,
set `sync_include = ["/frontend/build/***"]` explicitly. A leading / anchors the pattern at the project root;
*** includes the directory and all its contents. Do not include Windows installations, virtual environments or dependency caches.

Workdirs, Compose files, Python roots and classpath modules must be project-relative. Absolute paths and .. are rejected.
Execution also checks resolved paths for escapes from the project.

## Scan scope and version validation

Windows and WSL share static scanning rules. Scanning does not read `.gitignore` or execute project scripts, wrappers or downloaded content. Git-ignored local source can still participate in the project.

Root configuration is always included. Manual workflows inspect task/service workdirs, Python roots and necessary metadata along their ancestors up to the project root. Declared Node workspaces, pnpm packages, uv workspace members and Maven modules expand directly without the fallback depth limit. Undeclared side directories do not affect manual workflows. Fallback traversal applies without a project declaration or with discovery=auto. Nested Git repositories and independent projects containing dev-tools.toml are boundaries unless explicitly included.

```toml
[scan]
roots = ["frontend", "backend", "tools/local-api"]
exclude = ["**/fixtures/**", "tools/review-copies/**"]
max_depth = 4
```

| Field | Default | Meaning |
|---|---|---|
| roots | Inferred | Override fallback discovery starting directories. An explicit [] disables fallback discovery without excluding declared tasks or workspace members |
| exclude | [] | Additional metadata exclusions; conflicts with declared directories, required metadata or workspace members are errors |
| max_depth | 4 | Each fallback starting directory is depth 0; accepts 0–32 and reports warnings and truncated directories at the limit |

Paths are relative to the project root, use / and also accept normalized Windows backslashes. roots contains literal directories. exclude uses case-sensitive path globs with *, ? and **; a trailing / denotes a directory. Absolute paths, .., negation and .gitignore loading are unsupported. Dependency, build, cache and .local directories are skipped by default. Explicit workdirs, workspace members or roots can include these directories; symlinks, Windows junctions and escaping paths are always rejected. scan does not change builds.watch or sync_include/sync_exclude. Use sync_include separately for source in default synchronization exclusions. pnpm packages supports static block lists or quoted inline lists without evaluating YAML tags, anchors or dynamic expressions.

Root mise declarations authorize installation and execution. Conflicting declarations for one tool in both root mise files still block preparation. Package requirements, Java compilation targets and nested mise declarations within participating scopes are checked for compatibility: root Node 24.22.0 satisfies 24.x; incompatible or unverifiable constraints block explicitly. Java compilation targets are minimum JDK requirements. Development preferences such as .nvmrc, .tool-versions, Volta and devEngines do not override root declarations; differences produce warnings. Without root declarations, known ranges may infer a provably compatible selector. Equal-priority explicit version conflicts require a user decision; preparation still requires root declarations.

scan/init/check JSON includes scope: scopes lists directories and root/workflow/workspace/discovery/scan.roots inclusion reasons, files lists inspected metadata and truncated lists directories omitted by the fallback limit. check also includes warnings and does not imply installation or startup. Each plan reuses its directory inventory and parsed metadata. Subsequent operations observe additions, removals and edits without a persistent scan cache.

## Automatic dependency maintenance

The default is `dependency_mode = "auto"`. Startup and declaration changes while running trigger preparation without a manual prepare command. Changes are tracked per module; service parameter edits do not reinstall dependencies. Known standard npm, pnpm, Yarn, Bun and uv install tasks can generate or update lockfiles before running the original installation task. Lockfile-only changes retain locked versions instead of upgrading to latest. Maven installs from the POM; generated pip workflows recreate the project .venv, while uv sync synchronizes dependencies exactly. Custom scripts keep their commands, and custom package selections or arguments are not rewritten; they must implement their own cleanup semantics.

WSL resolves locks in its Linux workspace and copies back only known lockfiles after checking for concurrent source edits. Dependency directories and caches remain native. Updates may briefly stop services. The monitor stays alive after failure and waits for corrections; explicit prepare can retry manually. Set `dependency_mode = "locked"` to require existing locks and prevent automatic resolution or writeback.

Configurations generated by `init` contain `discovery = "auto"`. Registration records a discovery baseline; subsequent module additions/removals merge into task, service and build graphs. Only fields still matching their generated values are updated, preserving customized commands and deliberately deleted entries. Existing custom declarations default to `discovery = "manual"` and are not rewritten. First adopting auto establishes a baseline while keeping current declarations. Automatic configuration updates reserialize TOML, preserving values but not original comments or formatting.

Unresolved entrypoints, conflicts, and new languages without root mise version declarations require correction. Tool versions and business services are not guessed. `scan` and `check` analyze read-only; `init` only creates missing configuration. None installs anything.

## Commands and platform overlays

Prefer argv arrays. Each item is one argument; arguments are not joined into shell expressions:

```toml
[tasks.install]
command = ["npm", "ci"]
tools = ["node"]
```

For shell expressions, use a string command and explicitly choose bash or pwsh.
An argv array cannot also set shell. Tasks and services accept platforms.windows / platforms.wsl overlays:

```toml
[tasks.message]
command = "printf '%s' ready"
shell = "bash"

[tasks.message.platforms.windows]
command = "Write-Output ready"
shell = "pwsh"
```

The selected shell must already exist on the target platform. Overlay env values merge by key; other fields replace the original value.
CLI environment values are win/wsl; overlay names remain windows/wsl.

Argv commands support these placeholders; script strings do not expand them:

| Placeholder | Resolution |
|---|---|
| {python} | python on Windows or python3 in WSL, using the project version with mise |
| {venv_python} | Native .venv Python, rooted at python.root or the command workdir |
| {maven} | Native Maven Wrapper when java options are configured, otherwise mvn |
| {spring_classpath} | Prepared Spring DevTools, dependency class overlays and trigger directory |

## Tasks: tasks.NAME

| Field | Default or requirement | Meaning |
|---|---|---|
| command | Required | Argv array, or script with explicit shell |
| shell | Empty | bash/pwsh for script commands only |
| workdir | . | Working directory relative to the project root |
| depends_on | [] | Tasks to execute first |
| role | prepare | prepare participates automatically; build is normally referenced by build policies |
| manager | Empty | Package manager identifier for preparation lockfile validation |
| tools | [] | Required tools, such as node, python, uv, java or maven |
| env | Empty table | Task environment variables; values must be strings |
| java / python | Empty tables | Native driver options below |

Prepare runs every role=prepare task, service prepare references and the dependencies of those tasks.
A role=build task explicitly referenced by service prepare or task dependencies also joins the preparation plan.
Tasks run in dependency order; output goes to logs/tasks/NAME.log under native state.

For npm/pnpm/yarn/bun/uv managers, the plan requires the corresponding lockfile in workdir.
An explicit workflow takes its tool requirements from tasks/services; a leftover uv.lock does not require uv for a pip workflow. Metadata parse errors and version conflicts still block preparation, and tools actually used must be declared in the root mise file.
Automatic scanning, discovery and dependency fingerprints skip `.local/` by default; explicit project scopes can include local source. File monitoring and default WSL synchronization retain their own exclusions. Version conflicts within participating project scopes report versions and source files.
Workspace discovery installs at the package workspace root and runs each application in its own directory; set workdir explicitly in custom declarations.
Lockfile validation does not rewrite arbitrary install commands into frozen installs. Declare npm ci, --frozen-lockfile, --immutable or uv sync --locked in command.

## Services: services.NAME

| Field | Default or requirement | Meaning |
|---|---|---|
| driver | process | Native process or compose orchestration |
| command | Required for process | Same command format as tasks; optional for compose |
| shell / workdir / tools / env | Same as tasks | Command execution settings |
| prepare | [] | Required preparation tasks |
| depends_on | [] | Services to start and await first |
| restart | on-failure | never, on-failure or always |
| required | true | Whether readiness contributes to the project; optional services still start by default |
| health | Empty table | TCP/HTTP health checks |
| java / python / compose | Empty tables | Native driver options |

Service dependencies and task dependencies form separate graphs. Start --service includes service dependencies; stop stops the whole project.
In the current project directory, use `dev-tools logs --service __sync` or `dev-tools logs --service __watch` for monitor logs. You can also name the project explicitly, for example `dev-tools logs my-app __watch`.

Health supports:

- tcp: a port from 1 to 65535, probed on 127.0.0.1.
- http: an HTTP(S) URL requiring 2xx, using a direct connection without redirects.
- json: an optional nonempty expected-field table requiring http. Responses must contain matching fields and types; extra fields are allowed. Reads at most 64 KiB and checks an overall deadline between reads so trickling bodies cannot stall queries; responses are not saved to state or logs.
- timeout: readiness wait in seconds, default 15, greater than 0 and at most 300.

- details: an optional array of JSON object field paths, using `/` separators and JSON Pointer `~0/~1` escapes; only object fields are traversed. Requires http, with up to 16 paths, 128 characters and eight segments per path. For example, `details = ["/checks/storage", "/checks/queue"]`. Only field names with false or fixed failure values (failed/error/unhealthy/unavailable/down) are reported; response values and bodies are excluded. Details may be read from non-2xx responses within the same 64 KiB and read deadline limits. Details are advisory and do not change existing readiness decisions.

When both TCP and HTTP are declared, both must succeed. Native processes without a probe have health unknown; process readiness and confirmed health are reported separately. Compose also checks container state and container-reported health. Each container-state query is limited to 5 seconds; failure or timeout reports unhealthy.

## Desktop clients and shared services

See [dev-desktop.toml](../examples/dev-desktop.toml). Use a manual workflow with frontend.depends_on=["backend"] and desktop.depends_on=["frontend"], waiting for each dependency. Declare tcp together with http/json: startup rejects occupied declared ports without adopting or terminating their owners, and HTTP JSON validates application identity and readiness. The application validates its own project session; dev-tools does not read tokens or application session files.

```toml
[services.backend.health]
tcp = 7877
http = "http://127.0.0.1:7877/health"
timeout = 180
json = { application = "desktop-app", ready = true }

[services.desktop]
command = ["npm", "run", "tauri:dev"]
workdir = "frontend"
depends_on = ["frontend"]
tools = ["node", "rust"]
required = false
restart = "never"
```

required=false keeps default startup enabled while allowing a closed client without making shared services unready. An optional service required by another required service is still checked as a required dependency. restart=never prevents automatic relaunch on exit. Optional startup failure preserves running shared services and monitors while explicit start returns 1. Background preparation restoration records optional client failures on that service. Maintenance does not reopen closed optional clients.

These rules apply to any service name or driver. A required dependency must remain running and pass readiness checks even when it declares required=false; exit code 0 does not establish readiness. A required service or monitor startup failure rolls back only workers newly started by that attempt, preserving previously running services. Recovery records retain both startup and cleanup errors; explicit stop cancels pending restoration.

For optional-only projects, closing the client still leaves the project listed as running while preparation remains valid and maintenance monitors are active. stop ends both services and maintenance.

```text
dev-tools start desktop-app
dev-tools start desktop-app --service frontend
dev-tools start desktop-app --service desktop
dev-tools stop desktop-app
```

The second command starts or retains shared services; the third reopens the client and ensures its dependencies run. Repeated start preserves active services without duplicate instances. Without desktop.health, running only indicates a live launcher. To confirm CDP, configure tcp=9222 and http="http://127.0.0.1:9222/json/version" for the Windows desktop service. The application/Tauri owns display, CDP binding and initial Rust compilation. Native WSL desktop development also requires a graphical session and application system libraries.

Existing per-service env passes project configuration literally without shell expansion. Application entrypoints interpret shared-backend mode, source directories and .local/data, .local/dev and .local/reports. Development clients connect to the shared backend; the application retains release-sidecar ownership. Tauri watches Rust source and restarts its client without duplicate dev-tools build tasks. Runtime and dependency declaration changes still use project preparation maintenance.
Tasks and services also receive absolute DEV_TOOLS_SOURCE_ROOT (source checkout) and DEV_TOOLS_PROJECT_ROOT (native runtime workspace), derived from native bindings and protected against project env overrides. They usually match on Windows and may differ on WSL. Applications can use these identities to choose data/session locations; dev-tools does not create business directories or save tokens.

Windows service and preparation children hide consoles. Each worker owns a separate Job Object, keeping client closure and stop isolated from other services; WSL uses native process groups and systemd control groups. Project stop terminates managed processes and releases their operating-system file locks without deleting application data, lock files or session markers.
Hidden workers and GUI windows are handled separately: Windows children explicitly request SW_SHOWNORMAL to avoid inheriting the launcher's hidden state, while CREATE_NO_WINDOW hides consoles. No desktop-specific window switch is needed, and existing windows are unchanged.

Rust supports root mise declarations, rust-toolchain/rust-toolchain.toml, Cargo.toml minimum rust-version requirements and Cargo workspace members. Participating frontend directories discover src-tauri/Cargo.toml with a tauri scope reason; old .local copies remain excluded by default. cargo/rustc commands require Rust. Declare tools=["node","rust"] when starting Tauri through npm; npm ci installs the project-local Tauri CLI. dev-tools does not execute npm scripts to infer tools, generate Cargo install tasks or automatically upgrade the compiler.

## Native driver options

### Python

```toml
[services.api.python]
root = "."
```

Root is project-relative and selects the directory containing .venv. Omission uses the command workdir.
Windows uses .venv/Scripts/python.exe; WSL uses .venv/bin/python.
Preparation tasks create or synchronize the environment; startup does not synchronize packages. See [dev-python-web.toml](../examples/dev-python-web.toml) for uv.

### Maven and Spring

```toml
[tasks.install-java.java.maven]
repository = "user"

[services.api.java.maven]
repository = "user"

[services.api.java.spring]
devtools = "org.springframework.boot:spring-boot-devtools:3.5.0"
classpath_modules = ["backend/library/target/classes"]
classpath_entries = []
```

Configure Maven options separately on the relevant tasks and services. Repository supports:

- user: the runtime user's native ~/.m2/repository, the default.
- project: a repository under ~/.cache/dev-tools/maven, isolated by native storage identity. Rename preserves that identity.

With java options configured, {maven} searches from workdir to the project root for native mvnw.cmd or mvnw, then falls back to mvn.
Without a Wrapper, mise projects need a root Maven version; system projects use an existing Maven.

Spring devtools is a group:artifact:version coordinate; match the example version to the target project's Spring Boot metadata.
Discovery emits an unresolved diagnostic when it cannot determine the version; resolve it before prepare.
The shared preparation flow supplies the JAR, including automatic startup and background dependency maintenance. Ordinary partial builds do not download it.

Classpath modules are compiled output directories relative to the project root.
Classpath entries optionally filters immediate directory names within those outputs; [] disables filtering.
Only .class files are copied to native state, using atomic replacement per file. The shared trigger file updates after copying.
The application command must reference {spring_classpath} and configure the same dev-tools-reload.trigger. See [dev-java-web.toml](../examples/dev-java-web.toml) for a complete declaration.

### Compose

```toml
[services.stack]
driver = "compose"

[services.stack.compose]
files = ["compose.yaml"]
profiles = []
build = true
pull = false
```

Files are relative to the service workdir, defaulting to ["compose.yaml"]; profiles selects Compose profiles.
The shared preparation flow handles pull/build, defaulting to build without pull. Start can prepare images automatically.
Native storage identity creates a stable Compose project name.
Windows requires a configured native Docker engine. WSL prepare can supply requested Docker/Compose prerequisites.
Declare business services such as Redis/MySQL in the project's own Compose files; the scanner does not infer them.

## Build monitoring: builds.SERVICE

```toml
[builds.api]
watch = ["backend/app/src/main", "backend/library/src/main", "backend/pom.xml"]
source_task = "compile-java"
resource_task = "resources-java"
structural_task = "clean-java"
extensions = [".java", ".xml", ".yaml", ".yml", ".properties"]
resources = [".xml", ".yaml", ".yml", ".properties"]
structural = ["pom.xml", "**/pom.xml", ".mvn/**", "**/.mvn/**"]
```

SERVICE must name a declared service. All three task fields must reference existing tasks, normally with role=build.
Watch must be nonempty and may contain project-relative files or directories. Extensions/resources/structural may be omitted for defaults. The structural list above is a custom example; defaults also include Maven Wrapper patterns.

- Extensions filters monitored file suffixes; structural pattern matches are also included.
- Added/deleted files or structural matches trigger structural builds.
- Resource suffixes or changes within resources directories trigger resource builds.
- Other monitored changes trigger source builds.

Source monitoring reads the platform's working directory: source directly on Windows, and the synchronized Linux workspace in WSL. Project declarations and Git state still come from the original source directory. WSL watch paths must be included in synchronization. Maintenance and builds refresh workspace snapshots afterward to avoid duplicate builds.

Idle synchronization backs off from 0.75 seconds to at most 3 seconds of waiting; native file monitoring backs off from 0.5 seconds to at most 1.5 seconds, returning to fast checks after changes. Scanning and build time are additional. The WSL sync worker publishes Git/dependency fingerprints in native state storage for the watch worker to reuse. Missing results, results older than 6 seconds, previous-start results and pre-maintenance results trigger fresh checks. Before native edits trigger a build or maintenance, Git/dependency state is checked directly again so cached observations cannot hide a just-started checkout. Dependency declarations and synchronization scopes still receive periodic full scans, covering additions, removals, upgrades and uncommitted files without relying on cross-filesystem notifications. File changes and directory creation/deletion keep polling fast; directory metadata alone does not. Include/exclude rules, native build directories and preparation behavior remain unchanged. The first edit after idling may take a few extra seconds.

Source builds keep processes running. Resource/structural builds stop affected services and dependents, then restore them after building.
Branch builds synchronize source and run preparation tasks and structural builds using prepared runtimes.
Run `dev-tools build [NAME]` for a full build, or add `--service SERVICE` to build one service.
The monitor classifies source, resource and structural changes automatically. Full builds refresh project packages and run structural tasks; service builds stop and restore affected services and dependents.
Service builds preserve the preparation fingerprint. After runtime versions change, modules are rediscovered or lock resolution is needed, run `start` or `prepare` first. Builds cannot mark uninstalled runtimes as prepared.
Compose images are rebuilt only during full builds without a selected service.

## Validate and modify

```text
dev-tools show --json
dev-tools check --json
dev-tools prepare
dev-tools start
dev-tools status --json
dev-tools logs my-app install --task
```

Preparation previews do not execute tasks, install runtimes or change registrations/caches. Platform adapters may make read-only Docker queries.
Unresolved scans, initialization or plans exit 1. Runtime failures and partial results exit 1; argument errors exit 2 and interruptions exit 130.
Status exit codes do not express service health; automation should inspect the JSON payload.
Config/sysinfo/report have their own JSON outputs; do not assume the project-command schema_version applies to them.
See the [CLI and automation contract](cli-contract.en.md) for result fields, error documents and exit codes.

See [examples](../examples/README.md) for more templates and [README](../README.en.md) for installation and preferences.
