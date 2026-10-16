# dev-tools

[简体中文](README.md) · **English**

[![License MIT](https://img.shields.io/badge/License-MIT-blue)](LICENSE) [![Windows](https://img.shields.io/badge/Platform-Windows-blue)](docs/installation.en.md) [![WSL Ubuntu](https://img.shields.io/badge/WSL-Ubuntu-brightgreen)](docs/installation.en.md) [![Python 3.14](https://img.shields.io/badge/Python-3.14-blue)](docs/installation.en.md) [![Version 0.5.5](https://img.shields.io/badge/Version-0.5.5-blue)](CHANGELOG.md)

A nearly seamless development deployment experience for Windows and WSL: discover dependencies, prepare environments, start services, and follow code and dependency declarations.
Add, remove, or upgrade dependencies and keep developing as project configuration synchronizes them automatically. A good fit for fast-moving Vibe Coding projects.
Idle projects reduce polling automatically and return to fast synchronization after changes; WSL dependencies and builds stay in native Linux directories.

## Supported projects

| Project type | Default support |
| --- | --- |
| Node.js / frontend | Generates dependency preparation and dev/start services from package.json; supports npm, pnpm, Yarn and Bun, with Next.js, Vite and React detection |
| Python | Generates uv or pip preparation from pyproject.toml; FastAPI projects can receive a Uvicorn reload service |
| Java / Maven | Detects multiple modules, Maven Wrapper and Spring Boot, generating preparation and hot deployment workflows |
| Docker Compose | Uses existing Compose declarations; automatic discovery prefers Compose |
| Rust / Tauri, Gradle, custom processes | Configure tasks and startup commands explicitly; Rust toolchains, Cargo workspaces and minimum versions are checked; Gradle supports JDK metadata scanning |

Languages can be combined; declared workspaces and modules participate in scanning. See the [desktop template](examples/dev-desktop.toml) for clients sharing backend services, or other [project examples](examples/README.md). Runtime versions still follow root mise configuration; ambiguous entrypoints require explicit configuration.

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

Omit `-e` for the current platform; add `-e win` / `-e wsl` to operate on either target from either side. Projects, SSH, configuration, system information, reports and controller maintenance use the same syntax. Native WSL registration and lifecycle control use `sudo`.

## Common commands

| Command | Purpose |
| --- | --- |
| `dev-tools list` | List current-environment projects; `--all` lists both |
| `dev-tools check my-app` | Inspect the preparation plan read-only |
| `dev-tools status my-app` | Inspect project status |
| `dev-tools logs my-app web --follow` | Follow service logs |
| `dev-tools stop my-app` | Stop services and automatic maintenance |
| `dev-tools unregister my-app` | Unregister a project |
| `dev-tools config edit` | Edit local preferences |
| `dev-tools ssh list` | List SSH hosts; show inspects sources, edit opens configuration, connect logs in |
| `dev-tools hosts list` | Inspect Windows hosts, enabled states and conflicts |
| `dev-tools ports list` | Inspect actual listening ports and processes; `show 3000` locates a conflict |
| `dev-tools which python` | Inspect command candidates and link targets on the target PATH |
| `dev-tools net info` | Inspect DNS, environment proxies and WSL networking; `check HOST:PORT` diagnoses a connection |
| `dev-tools upgrade` | Update the tool itself |
| `dev-tools -v` / `dev-tools help` | Show the version or help |

Omit the project name in a directory that uniquely matches it. `list --all` combines both environments; `start --all` starts inactive projects in the selected environment.
Windows queries of WSL `list/status` also check localhost access. Common web services must return an HTTP response; stalled forwarding appears as Windows access failed.
Running `ssh`, `hosts`, `ports`, `which` or `net` alone displays its help. Detailed commands are in [Operation](docs/usage.en.md).

```text
dev-tools ports show 3000 -e win
dev-tools which python -e wsl
dev-tools net check http://localhost:3000/health -e wsl
```

Port and PATH queries are read-only. Network diagnostics use direct connections and check DNS, TCP, TLS and HTTP separately. `hosts` manages Windows only; system writes request elevation when needed and create backups.
Enter `Y`, or add `--yes`, to stop affected projects and restore them after an update; `dev-tools upgrade -e wsl` upgrades WSL.
Chinese is the default. Set `language = "en"` through `dev-tools config edit` for English. Configuration and caches remain native to each environment.
Scanning does not read `.gitignore`. Workflows and workspaces determine scope; configure discovery roots, exclusions and depth under `[scan]` in the [project configuration](docs/project-config.en.md#scan-scope-and-version-validation).
Python, Vite and a reopenable desktop client can share one development lifecycle; see the [desktop template](examples/dev-desktop.toml).

## Documentation

[Installation and updates](docs/installation.en.md) · [Project configuration](docs/project-config.en.md) · [Operation](docs/usage.en.md) · [CLI contract](docs/cli-contract.en.md)

[Examples](examples/README.md) · [Development and contributing](docs/development.en.md) · [Changelog](CHANGELOG.md)
