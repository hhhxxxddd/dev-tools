# dev-tools

[中文](README.md) · [Changelog](CHANGELOG.md) · **v0.4.0**

dev-tools is a command-line tool for managing development projects on Windows and WSL.
It discovers project dependencies, prepares environments, and synchronizes source files or triggers reloads and rebuilds during development, reducing repeated setup when switching projects or Git branches.

## Features

- Supports Node.js, Maven / Spring Boot, Python / uv, and existing Docker Compose projects.
- Discovers tool versions, package managers and Maven module dependencies to generate development configuration you can review and edit.
- Manages development services and file watching: frontend hot updates, Python reloads, Spring compilation and reloads, and rebuilds after branch changes.
- Windows and WSL share commands while keeping runtimes, dependencies and caches separate.
- `list` displays projects in a table, `status` checks services, and `logs` shows their output.
- Chinese by default, with English available through configuration; JSON output is available for scripts.

## Dependency discovery: start with an existing project

Enter your project directory, scan it, and preview the configuration to be generated:

```text
dev-tools scan
dev-tools init --dry-run
dev-tools init
```

| Existing project files | What dev-tools recognizes |
|---|---|
| `mise.toml`, `.tool-versions`, `.nvmrc`, `.python-version` and similar files | Node, Java, Python and other tool versions, with declaration sources |
| `package.json` and lockfiles | npm / pnpm / Yarn / Bun, workspaces, `dev` / `start` scripts and common frontend frameworks |
| `pom.xml` and Maven Wrapper configuration | Java / Maven versions, Spring Boot apps, local module dependencies and source that needs compiling together |
| `pyproject.toml` and `uv.lock` | Python version requirements, uv workspaces or pip workflows, and FastAPI development services |
| Existing Compose files | Container service entrypoints; databases and other business services come from project declarations |

`scan` reports tool versions, sources, conflicts and missing requirements. `init` creates missing root mise files and `dev-tools.toml`, preserving existing files. Mixed projects can get separate frontend, Java and Python tasks; projects automatically selected for Compose use their existing container declarations.

Then inspect the preparation plan with `dev-tools prepare --dry-run` and run `dev-tools prepare`. Tool versions come from the root mise files; packages are installed by the declared npm, Maven, uv or other tasks. dev-tools does not replace package managers' resolution of third-party dependencies.

Scanning only reads files. Review and complete unresolved versions, complex Maven configuration, custom entrypoints and business services; equal-priority version conflicts produce an error. Gradle currently supports version scanning only and needs explicit build and startup tasks.

## Hot deployment: how changes take effect

After `dev-tools start`, development services and the project's declared monitors run in the background. Windows uses the source directory directly. WSL continuously synchronizes source into a Linux working directory, keeping dependencies, virtual environments and build outputs on each platform.

| Change | Behavior in the default discovered workflow |
|---|---|
| Frontend source | Vite / Next.js or another development server handles hot updates; capabilities depend on the project script |
| Python / FastAPI source | The generated Uvicorn `--reload` service reloads automatically; review the application entrypoint and dependencies |
| Spring Java source | Compiles the app and dependency modules, updates the classpath and trigger file, and lets Spring DevTools reload the app; this is not arbitrary JVM code replacement |
| Spring resources, added/deleted files or build structure | Runs resource or structural tasks, stops affected services and their dependents, and restores them afterwards |
| Git branch or HEAD | By default, waits for Git operations and files to settle, reruns preparation tasks and structural builds, then restores previously running services |

For example, after preparation and startup, edit code or switch branches directly:

```text
dev-tools prepare my-app
dev-tools start my-app
git switch feature/my-change
dev-tools status my-app
dev-tools logs my-app __watch --follow
```

`feature/my-change` is an example branch name. You can also trigger a build manually:

```text
dev-tools build my-app --kind source
dev-tools build my-app --kind branch
```

Hot deployment depends on startup commands and build monitoring in `dev-tools.toml`; some services need an interruption. Source builds keep supervised processes running; resource, structural and branch builds may briefly stop services. Background builds use prepared runtimes and never install new versions or download Spring DevTools. Run `prepare` again after tool versions or project declarations change. See [build monitoring](docs/project-config.en.md#build-monitoring-buildsservice) for configuration.

## Installation

### Windows: use Scoop

With Scoop installed, run:

```powershell
scoop bucket add dev-tools https://github.com/hhhxxxddd/dev-tools
scoop install dev-tools/dev-tools
dev-tools help
```

Scoop installs PowerShell 7, mise and the tool's own Python host. No manual Python environment setup is required.

> Scoop installation requires a published Release and bucket manifest. During release preparation, use the source installation below.

### When you need WSL projects

Prepare a WSL distribution with systemd enabled. Ubuntu is the default. Then run once from Windows:

```powershell
dev-tools self install -e wsl
dev-tools self status --json
```

There is no need to clone the repository again inside WSL. `dev-tools` and `dev-tools help` show deployment instructions when WSL deployment is missing.
For another distro, edit `[wsl].distro` with `dev-tools config edit`. Missing mise and rsync can be installed automatically on Debian / Ubuntu; install them first on other distributions.

### Install from source

You need PowerShell 7, Git and an existing WSL distribution. Scoop is also required if Windows mise is missing. Run in PowerShell:

```powershell
git clone https://github.com/hhhxxxddd/dev-tools.git
cd dev-tools
.\scripts\bootstrap.ps1
. $PROFILE
dev-tools help
```

Bootstrap installs both Windows and WSL, so a separate WSL deployment is unnecessary afterwards.
For another distro, use `.\scripts\bootstrap.ps1 -Distro <distribution>` and update your local distro preference.
See the [installation guide](docs/installation.en.md) for single-platform and manual installation.

## Quick start

Run in your project's directory:

```text
cd path/to/my-app
dev-tools scan
dev-tools init --dry-run
dev-tools init
dev-tools register
dev-tools prepare --dry-run
dev-tools prepare
dev-tools start
dev-tools status
```

`scan` checks version declarations. `init` creates missing configuration and preserves existing files. Review the results and configuration, then use `prepare` to install project tools and dependencies, followed by `start`. Scanning and initialization never execute project code or install runtimes.

Omitting `-e` selects the current platform. To control a WSL project from Windows:

```powershell
dev-tools -e wsl register .
dev-tools -e wsl prepare my-app
dev-tools -e wsl start my-app
dev-tools -e wsl list
```

Inside native WSL, registration, preparation and lifecycle changes require `sudo`. Read-only commands and preparation previews do not:

```bash
sudo dev-tools prepare my-app
sudo dev-tools start my-app
dev-tools list
```

Replace `my-app` with the actual registered name. You can omit it when the current directory or a subdirectory uniquely matches a registered project.

## Common commands

| Command | Purpose |
|---|---|
| `dev-tools scan` | Inspect required tool versions |
| `dev-tools init` | Create missing project configuration |
| `dev-tools register` | Add the current project to local management |
| `dev-tools prepare my-app` | Install project runtimes and prepare dependencies and build outputs |
| `dev-tools start my-app` | Start project services |
| `dev-tools stop my-app` | Stop project services |
| `dev-tools restart my-app` | Restart project services |
| `dev-tools build my-app --kind branch` | Run a branch rebuild manually and restore services |
| `dev-tools list` | List projects and states on the current platform |
| `dev-tools status my-app` | Check project and service status |
| `dev-tools logs my-app web --follow` | Follow logs for the web service |
| `dev-tools config edit` | Edit local preferences |
| `dev-tools help` | Show all commands |

Replace `web` with the service name in your project configuration. More parameter and JSON output examples:

```text
dev-tools help prepare
dev-tools list --json
dev-tools sysinfo
```

## Configuration

| File | Purpose |
|---|---|
| `dev-tools.toml` in the project root | How to install dependencies, build and start services |
| `mise.toml` / `.mise.toml` in the project root | Required project tool versions |
| User-local `config.toml` | Language, editor, WSL distro and other preferences |

Open local preferences with `dev-tools config edit`, for example:

```toml
language = "zh"

[wsl]
distro = "Ubuntu"
```

The language accepts `zh` / `en` and takes effect on the next invocation. Project configuration can be committed to Git; local preferences are stored separately on each platform.
See [project configuration](docs/project-config.en.md) and [configuration and operation](docs/usage.en.md) for all fields.

## Update and uninstall

Stop running projects on the target platform before updating. Scoop installations update each platform separately:

```powershell
dev-tools -e win stop my-app
scoop update dev-tools
dev-tools -e wsl stop my-app
dev-tools self update -e wsl
```

Run `start` again as needed afterwards. Source installations use `git pull --ff-only` followed by the corresponding installer. Preferences, registrations, caches and project runtimes remain.

Use `scoop uninstall dev-tools` to remove the Scoop Windows entrypoint. See the [installation guide](docs/installation.en.md) for WSL removal. Uninstalling Windows does not uninstall WSL.

## Documentation

- [Installation, updates and releases](docs/installation.en.md)
- [Project configuration reference](docs/project-config.en.md)
- [Preferences, reports and operation](docs/usage.en.md)
- [Project examples](examples/README.md)
- [Development and verification](docs/development.en.md)

## Contributing

Issues and Pull Requests are welcome. The controller uses Python 3.14.8 without third-party Python runtime dependencies.
Read the [development guide](docs/development.en.md) and [maintenance boundaries](AGENTS.md) before making changes.

## License

[MIT](LICENSE)
