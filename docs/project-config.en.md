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

On Windows, run `dev-tools register` and `dev-tools prepare --dry-run --json`, review the plan, then prepare and start.
Use sudo for native WSL mutations; previews do not require sudo.

## Root fields

| Field | Default or requirement | Meaning |
|---|---|---|
| schema | Integer 1, required | Declaration format version |
| name | Required | Default registration name |
| toolchain | mise | mise uses explicit root versions; system uses existing system tools |
| rebuild_on_branch | true | Run preparation tasks and structural builds after branch/HEAD transitions settle |
| sync_exclude | [] | WSL sync exclusion patterns that preserve custom native outputs |
| tasks | Empty table | Named tasks |
| services | Empty table | Named services |
| builds | Empty table | Build monitoring policies named after services |

Project, task and service names contain 1–63 characters, start with a lowercase letter or digit, and otherwise allow lowercase letters, digits, dots, underscores and hyphens. Windows reserved device names are rejected.
Unknown fields, missing dependencies and dependency cycles are rejected.

Workdirs, Compose files, Python roots and classpath modules must be project-relative. Absolute paths and .. are rejected.
Execution also checks resolved paths for escapes from the project.

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
| health | Empty table | TCP/HTTP health checks |
| java / python / compose | Empty tables | Native driver options |

Service dependencies and task dependencies form separate graphs. Start --service includes service dependencies; stop stops the whole project.
Monitor logs are available with `dev-tools logs __sync` or `dev-tools logs __watch`.

Health supports:

- tcp: a port from 1 to 65535, probed on 127.0.0.1.
- http: a URL beginning with http:// or https://.
- timeout: readiness wait in seconds, default 15, greater than 0 and at most 300.

When both TCP and HTTP are declared, both must succeed. Native processes without a probe have health unknown; process readiness and confirmed health are reported separately. Compose also checks container state and container-reported health.

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
Only explicit prepare supplies the JAR. Build and start do not download it.

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
Explicit prepare handles pull/build, defaulting to build without pull. Start uses prepared images.
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

Source builds keep processes running. Resource/structural builds stop affected services and dependents, then restore them after building.
Branch builds synchronize source and run preparation tasks and structural builds using prepared runtimes.
Use `dev-tools build [NAME] --kind source|resource|structural|branch` manually; the default is branch.
Selecting --service with the default branch kind performs a structural build for that service.

## Validate and modify

```text
dev-tools show --json
dev-tools prepare --dry-run --json
dev-tools prepare
dev-tools start
dev-tools status --json
dev-tools logs install --task
```

Preparation previews do not execute tasks, install runtimes or change registrations/caches. Platform adapters may make read-only Docker queries.
Unresolved scans or plans exit 2. Controller execution errors normally exit 1; argument errors exit 2.
Status exit codes do not express service health; automation should inspect the JSON payload.
Config/sysinfo/report have their own JSON outputs; do not assume the project-command schema_version applies to them.

See [examples](../examples/README.md) for more templates and [README](../README.en.md) for installation and preferences.
