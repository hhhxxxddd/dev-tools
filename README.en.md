# dev-tools

[简体中文](README.md) · **English**

[![License MIT](https://img.shields.io/badge/License-MIT-blue)](LICENSE) [![Windows](https://img.shields.io/badge/Platform-Windows-blue)](docs/installation.en.md) [![WSL Ubuntu](https://img.shields.io/badge/WSL-Ubuntu-brightgreen)](docs/installation.en.md) [![Python 3.14](https://img.shields.io/badge/Python-3.14-blue)](docs/installation.en.md) [![Version 0.5.0](https://img.shields.io/badge/Version-0.5.0-blue)](CHANGELOG.md)

Hot deployment and updates for Windows and WSL development projects: discover dependencies, prepare environments, start services, and follow code and dependency changes.
Supports Node.js, Python / uv, Maven / Spring Boot, and existing Docker Compose projects.

## Requirements

| Platform / dependency | Version or requirement | Scope |
| --- | --- | --- |
| Windows | Windows 11 | Native Windows controller |
| WSL (optional) | WSL 2; verified on Ubuntu 24.04 LTS | Enable systemd when managing WSL projects |
| PowerShell | 7 or newer | Windows entrypoint and process control |
| Python | `>=3.14.8,<3.15` | Standard-library-only controller; existing native interpreters can be bound |
| Git | Available `git` command | Source installation, tool updates, and branch monitoring |
| mise | Available `mise` command | Default host installation and project runtimes |
| rsync | Available `rsync` command | WSL deployment and source synchronization |
| Docker / Compose (optional) | Docker and Compose v2 | Required for Compose projects |
| Scoop (optional) | Available `scoop` command | Required for Scoop installation |

Project Node.js, Java, and other versions follow root mise configuration.

## Installation

### Scoop (Windows)

Install official releases through hxd-box:

```powershell
scoop bucket add hxd-box https://github.com/hhhxxxddd/hxd-box
scoop install hxd-box/dev-tools
```

Automatically prepares Git, PowerShell 7, mise, and the Python 3.14 controller host.

### Git (Windows)

Prepare the dependencies above, then obtain and install the current source:

```powershell
git clone https://github.com/hhhxxxddd/dev-tools.git
cd dev-tools
.\scripts\install.ps1
```

Reopen PowerShell after installation. See the [installation guide](docs/installation.en.md) for existing Python bindings and WSL-only installation.

Both installation methods can deploy to WSL from Windows:

```powershell
dev-tools self update -e wsl
```

Ubuntu is the default. Windows and WSL keep their runtimes, dependencies, and caches separately.

## Quick start

Run inside your project:

```text
cd path/to/my-app
dev-tools init
dev-tools register
dev-tools start
```

`init` preserves existing configuration; `start` prepares and starts services. Reloads and dependency changes then follow your configuration.
Tasks and services live in `dev-tools.toml`; runtime versions live in root `mise.toml` / `.mise.toml`.

Omit `-e` for the current platform; add `-e wsl` to manage WSL projects from Windows. Native WSL registration and lifecycle control use `sudo`.

## Common commands

| Command | Purpose |
| --- | --- |
| `dev-tools list --all` | List Windows and WSL projects |
| `dev-tools check my-app` | Inspect the preparation plan read-only |
| `dev-tools status my-app` | Inspect project status |
| `dev-tools logs my-app web --follow` | Follow service logs |
| `dev-tools stop my-app` | Stop services and automatic maintenance |
| `dev-tools unregister my-app` | Unregister a project |
| `dev-tools config edit` | Edit local preferences |
| `dev-tools upgrade` | Update the tool itself |
| `dev-tools -v` / `dev-tools help` | Show the version or help |

Omit the project name in a directory that uniquely matches it; replace the sample project and service names.
`list` shows the current platform; `list --all` shows Windows and WSL.
Stop projects on the affected platforms before updating the tool; use `dev-tools upgrade -e wsl` to update WSL.
Chinese is the default; set `language = "en"` through `config` for English.

## Documentation

[Installation and updates](docs/installation.en.md) · [Project configuration](docs/project-config.en.md) · [Operation](docs/usage.en.md) · [CLI contract](docs/cli-contract.en.md)

[Examples](examples/README.md) · [Development and contributing](docs/development.en.md) · [Changelog](CHANGELOG.md)
