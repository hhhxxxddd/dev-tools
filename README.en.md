# dev-tools

[中文](README.md) · [Changelog](CHANGELOG.md) · **v0.4.0**

**Discover project dependencies, reload after code changes, and keep developing after dependency upgrades.**

dev-tools manages development projects on Windows and WSL. It suits **vibe coding** workflows where AI repeatedly changes code, adds dependencies and switches branches. Let the tool handle environment preparation and services so you can see changes sooner.

## Core capabilities

- **Discover dependencies automatically**: read existing version, package manager and build declarations, identify runtimes and Maven module dependencies, and generate configuration you can review.
- **Reload after code changes**: frontend development servers handle hot updates, Python reloads automatically, and Spring compiles and reloads through DevTools. Branch changes trigger configured rebuilds.
- **Keep developing after upgrades**: update dependency declarations, lockfiles and any required tool version configuration, then run `prepare`. Reuse the registration and working directory, prepare the environment again, and restore previously running services after success.

Supports Node.js, Maven / Spring Boot, Python / uv and existing Docker Compose projects. Windows and WSL share commands while keeping runtimes, dependencies and caches separate.

## Installation

Source installation is currently available. Run in PowerShell:

```powershell
git clone https://github.com/hhhxxxddd/dev-tools.git
cd dev-tools
.\scripts\bootstrap.ps1
. $PROFILE
dev-tools help
```

You need PowerShell 7, Git and WSL with systemd enabled; Ubuntu is the default. Scoop is required if Windows mise is missing. Bootstrap deploys both Windows and WSL.

Once the Release and bucket manifest are published, you can also use Scoop:

```powershell
scoop bucket add dev-tools https://github.com/hhhxxxddd/dev-tools
scoop install dev-tools/dev-tools
```

Scoop prepares the Windows controller and its own Python host. If you need WSL, deploy it once from Windows:

```powershell
dev-tools self install -e wsl
```

Skip this if source bootstrap already deployed WSL. No second repository clone is needed inside WSL. `dev-tools` and `dev-tools help` prompt when WSL deployment is missing. See the [installation guide](docs/installation.en.md) for single-platform installation, other distributions and updates.

## Quick start

Enter an existing project directory:

```text
cd path/to/my-app
dev-tools scan
dev-tools init --dry-run
dev-tools init
dev-tools register
dev-tools prepare --dry-run
dev-tools prepare
dev-tools start
```

Review the scan results and generated configuration before preparing and starting the project. `init` preserves existing files; `scan` and `init` never execute project code or install runtimes. Add missing versions or custom entrypoints to project configuration.

Omitting `-e` selects the current platform. To control WSL projects from Windows:

```powershell
dev-tools -e wsl register .
dev-tools -e wsl prepare my-app
dev-tools -e wsl start my-app
```

Replace `my-app` with the registered name; omit it in a directory that uniquely matches a project. Inside native WSL, registration, preparation and lifecycle changes require `sudo`; read-only commands and preparation previews do not.

## Keep developing

After startup, edit code or switch Git branches directly. The tool synchronizes source, triggers reloads or rebuilds according to project configuration. Dependency upgrades do not require registering the project again. Update declarations and lockfiles, then run:

```text
dev-tools prepare my-app --dry-run
dev-tools prepare my-app
dev-tools status my-app
```

Runtime versions belong in root `mise.toml` / `.mise.toml`; installation, build and startup workflows belong in `dev-tools.toml`. Reload capabilities depend on the framework and configuration, and some rebuilds briefly stop services. New runtime versions are installed by explicit `prepare`.

## Common commands

| Command | Purpose |
|---|---|
| `dev-tools list` | View projects and states in a table |
| `dev-tools status my-app` | Check service status |
| `dev-tools logs my-app web --follow` | Follow service logs; replace web with the service name |
| `dev-tools build my-app --kind branch` | Trigger a full branch rebuild manually |
| `dev-tools stop my-app` | Stop the project |
| `dev-tools help` | Show all commands |

Chinese is the default. Use `dev-tools config edit` to set `language = "en"` for English; preferences are stored separately on each platform.

## Documentation and contributing

- [Installation, updates and removal](docs/installation.en.md)
- [Project configuration and reload policies](docs/project-config.en.md)
- [Preferences, reports and operation](docs/usage.en.md)
- [Project examples](examples/README.md)
- [Development and verification](docs/development.en.md)

Issues and Pull Requests are welcome. Read the [maintenance boundaries](AGENTS.md) before contributing.

## License

[MIT](LICENSE)
