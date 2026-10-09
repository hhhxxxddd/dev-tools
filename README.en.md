# dev-tools

[中文](README.md) · [Changelog](CHANGELOG.md) · [v0.4.0](https://github.com/hhhxxxddd/dev-tools/releases/tag/v0.4.0)

**Nearly seamless local development: discover dependencies, reload code changes, and keep going after dependency upgrades.**

A development project manager for Windows and WSL, suited to **vibe coding** workflows where AI repeatedly changes code, adds dependencies and switches branches. Register and start once, then focus on your code while dev-tools handles dependency synchronization and service recovery.

- **Discover automatically**: read existing declarations, identify Node.js, Maven / Spring Boot, Python / uv and existing Docker Compose projects, and generate configuration you can review.
- **Reload changes**: frontend hot updates, Python reloads, and Spring compilation with DevTools reloads; branch changes trigger configured rebuilds.
- **Follow dependency changes**: synchronize lockfiles per module, install additions or upgrades, remove obsolete packages and restore services while running. No re-registration or manual preparation is needed; customized commands are preserved.

## Installation

On Windows, install with Scoop:

```powershell
scoop bucket add dev-tools https://github.com/hhhxxxddd/dev-tools
scoop install dev-tools/dev-tools
dev-tools help
```

Scoop installs Git, PowerShell 7, mise and the controller's own Python. Project runtimes follow root version declarations; Windows and WSL store dependencies and caches separately.

For WSL, install a distro and enable systemd, then deploy once from Windows:

```powershell
dev-tools self update -e wsl
```

The same command handles first installation and updates, without cloning again inside WSL. Ubuntu is the default; `dev-tools` and `help` prompt when deployment is missing. See the [installation guide](docs/installation.en.md) for source installation and other distros.

## Quick start

Enter an existing project directory:

```text
cd path/to/my-app
dev-tools init
dev-tools register
dev-tools start
```

`init` preserves existing configuration and reports missing exact versions or custom entrypoints; `start` prepares the environment automatically. Then edit code, add, remove or upgrade dependencies, or switch branches. Dependency updates may briefly stop services; reload behavior depends on the framework and configuration.

Omit `-e` to use the current platform; control WSL projects from Windows:

```powershell
dev-tools -e wsl register .
dev-tools -e wsl start
```

Native WSL registration and lifecycle control require `sudo`. `stop` disables automatic maintenance; the next `start` catches up with changes.

## Everyday use

```text
dev-tools list
dev-tools status my-app
dev-tools logs my-app web --follow
dev-tools stop my-app
```

Replace the sample project and service names; omit the project name in a directory that uniquely matches it. Preview preparation with `dev-tools prepare --dry-run`; build the whole project manually with `dev-tools build`.

Workflows live in `dev-tools.toml`, runtime versions in root `mise.toml` / `.mise.toml`. Scanning and initialization never execute project code or install runtimes. Set `dependency_mode = "locked"` to disable automatic lockfile changes. Chinese is the default; use `dev-tools config edit` to set `language = "en"` for English.

## Updates

Stop projects on the platform you are updating first:

```powershell
scoop update
scoop update dev-tools
dev-tools self update -e wsl
```

Update WSL when needed. Both platforms retain registrations, preferences, caches and project runtimes; use `start` to restore services afterward.

## Documentation and contributing

[Installation and removal](docs/installation.en.md) · [Project configuration and reloads](docs/project-config.en.md) · [Operation](docs/usage.en.md) · [Examples](examples/README.md)

Issues and Pull Requests are welcome; read [development and verification](docs/development.en.md) and the [maintenance boundaries](AGENTS.md) before contributing.

[MIT](LICENSE)
