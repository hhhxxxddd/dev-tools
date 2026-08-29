# dev-tools

[中文](README.md) | [English](README.en.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

`dev-tools` is a unified command entrypoint for a personal Windows and WSL development
environment. It organizes shared runtime management through mise and normalizes existing project
version declarations into a project-level `mise.toml`.

It does not replace Scoop, winget, APT, Maven, pnpm, uv, or `wsl-devctl`, and it never executes
scripts found in a scanned project.

## Companion Project

[`wsl-devctl`](https://github.com/hhhxxxddd/wsl-devctl) safely mirrors Windows source into WSL ext4
and manages project builds, systemd processes, and live reload. `dev-tools` supplies its shared
runtime declarations and project-level `mise.toml`.

## Installation

Clone the repository to a location accessible from both Windows and WSL:

```powershell
git clone https://github.com/hhhxxxddd/dev-tools.git
cd dev-tools
```

The recommended PowerShell bootstrap initializes Windows, Ubuntu WSL, and shared runtimes in one
command:

```powershell
.\scripts\bootstrap.ps1 -InstallWslDevctl
```

It will:

1. Install Windows mise through Scoop when missing.
2. Install WSL mise through the officially documented
   [`extrepo + apt`](https://mise.jdx.dev/installing-mise.html#apt) method when missing.
3. Install the `dev-tools` entrypoint on Windows and WSL.
4. Install platform-native runtimes declared by the shared `config/mise.toml`.
5. Optionally install the sibling `wsl-devctl` checkout and add a transparent PowerShell forwarder.

Common options:

```powershell
.\scripts\bootstrap.ps1 -Distro Ubuntu -InstallWslDevctl
.\scripts\bootstrap.ps1 -InstallWslDevctl -WslDevctlPath E:\Projects\MyProjects\wsl-devctl
.\scripts\bootstrap.ps1 -SkipRuntimeInstall
```

Windows and WSL binaries, caches, and PATH values remain platform-native. Bootstrap unifies the
installation workflow, version declarations, and command entrypoints.

Use the individual installers when only one side is needed.

Install the PowerShell entrypoint:

```powershell
.\scripts\install.ps1
```

Install the WSL entrypoint:

```bash
sudo bash scripts/install.sh
```

The installers resolve the repository's actual location and do not embed a username or drive
letter. Use the matching `uninstall.ps1` or `uninstall.sh` to remove an entrypoint. Installed
runtimes and caches are preserved.

After installing `wsl-devctl`, PowerShell can call it directly:

```powershell
wsl-devctl help
wsl-devctl status local-my-app
```

The command is transparently forwarded to the configured WSL distribution.

## Environment Management

From PowerShell, manage both Windows and Ubuntu WSL:

```powershell
dev-tools status
dev-tools install
dev-tools outdated
dev-tools upgrade
dev-tools prune
dev-tools doctor
```

The same commands run inside WSL manage only the current WSL environment.

Shared defaults live in [`config/mise.toml`](config/mise.toml). Windows and WSL use the same
declarations while installing platform-native runtimes into separate data and cache directories.

## Project Version Discovery

Scan and report without writing files:

```bash
dev-tools project scan .
dev-tools project scan . --json
```

Preview or generate a project configuration:

```bash
dev-tools project init . --dry-run
dev-tools project init .
```

The scanner currently recognizes:

- `mise.toml`, `.mise.toml`, and `.tool-versions`.
- Java declarations in `.java-version`, `.sdkmanrc`, Maven POM files, and Gradle toolchains.
- Maven Wrapper; when present, no duplicate mise Maven declaration is generated.
- Node declarations in `.nvmrc`, `.node-version`, and `package.json` engines, devEngines, or Volta.
- npm, pnpm, and Yarn versions from `packageManager`, engines, and Volta declarations.
- Python declarations in `.python-version`, `pyproject.toml`, and `uv.lock`, including uv's own
  required version.

Safety rules:

- Parse only known text, TOML, JSON, and XML metadata. Never execute project code.
- Preserve an existing root `mise.toml` or `.mise.toml` byte-for-byte.
- Stop and report equal-priority version conflicts instead of generating a file.
- Convert version ranges into auditable broad versions, such as `>=3.11` to `python = "3.11"`.
- Include declaration sources in scan and generated output for review before committing.

## Relationship with wsl-devctl

- `dev-tools` discovers and generates project version declarations.
- [`wsl-devctl`](https://github.com/hhhxxxddd/wsl-devctl) consumes those declarations and manages
  Windows source mirroring, systemd, builds, live reload, and recovery.
- The projects cooperate through a CLI/JSON contract and do not import each other's Python modules.

Typical workflow:

```bash
dev-tools project init /mnt/e/Projects/CompanyProjects/order-service
wsl-devctl init /mnt/e/Projects/CompanyProjects/order-service \
  --toolchain mise --fix --start
```

Use the integrated command when both tools are installed:

```bash
wsl-devctl init /mnt/e/Projects/CompanyProjects/order-service \
  --toolchain mise --generate-mise --fix --start
```
