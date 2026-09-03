# dev-tools

[简体中文](README.md) · **English**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

`dev-tools` is a project toolchain entrypoint for Windows and WSL. It detects Java, Node.js,
Python, Maven, and package-manager versions from files already in a project, generates a
project-level `mise.toml`, and installs missing versions only when preparation is explicitly
requested.

## Design boundaries

- Never create or install global project defaults for Java, Node.js, Python, Maven, or other
  runtimes.
- `scan` and `init` parse known metadata only. They do not execute project code or download
  runtimes.
- Only an explicit `project prepare` call may ask mise to install versions.
- Windows and WSL share project declarations while keeping native binaries and caches separate.
- On Windows, `dev-tools` uses a private, globally inactive Python 3.11 for its own scanner. It
  does not participate in project version selection.
- Codex, Claude Code, and CodeGraph are Windows operator CLIs with a separate config and host Node;
  `project prepare` explicitly ignores that config.

## Command help

Start with these entrypoints:

```powershell
dev-tools help
dev-tools project --help
dev-tools project prepare --help
```

| Command | Writes files | Downloads | Purpose |
|---|---:|---:|---|
| `dev-tools --version` | No | No | Show the current version |
| `dev-tools status` | No | No | Check mise on Windows and WSL |
| `dev-tools doctor` | No | No | Run mise diagnostics on both sides |
| `dev-tools cli status` | No | No | Inspect Windows operator CLIs |
| `dev-tools cli install` | No | Yes | Explicitly install missing operator CLIs |
| `dev-tools cli outdated` | No | No | Check operator CLI updates |
| `dev-tools cli upgrade` | No | Yes | Explicitly update operator CLIs |
| `dev-tools project scan [PATH]` | No | No | Report project versions, sources, and conflicts |
| `dev-tools project init [PATH]` | Maybe | No | Generate a root `mise.toml` when missing |
| `dev-tools project prepare [PATH] --dry-run` | No | No | Preview versions required by this project |
| `dev-tools project prepare [PATH]` | No | Yes | Install missing versions declared by this project |

Commands use the current directory when `PATH` is omitted. PowerShell `status` and `doctor`
check Windows and the `Ubuntu` WSL distribution by default; pass `-Distro` to select another
distribution. The WSL entrypoint checks the current WSL environment only.

## Installation

Clone the repository somewhere accessible from both Windows and WSL:

```powershell
git clone https://github.com/hhhxxxddd/dev-tools.git
cd dev-tools
.\scripts\bootstrap.ps1
```

Install the companion
[`wsl-devctl`](https://github.com/hhhxxxddd/wsl-devctl) at the same time:

```powershell
.\scripts\bootstrap.ps1 -InstallWslDevctl
```

Bootstrap will:

1. Install Windows mise through Scoop when missing.
2. Install WSL mise and Python 3 through `extrepo + apt` when missing.
3. Install the `dev-tools` command entrypoint on Windows and WSL.
4. Install the private Windows Python used only by the `dev-tools` scanner.
5. Optionally install `wsl-devctl` and create its PowerShell forwarding command.

It does not scan projects, install project runtimes, or automatically install Windows operator
CLIs. Run `dev-tools cli install` explicitly when those CLIs are wanted.

Install only one command entrypoint when needed:

```powershell
.\scripts\install.ps1
```

```bash
sudo bash scripts/install.sh
```

## Project workflow

### 1. Inspect detected versions

```powershell
dev-tools project scan E:\Projects\MyProjects\some-project
dev-tools project scan E:\Projects\MyProjects\some-project --json
```

### 2. Generate a project declaration

```powershell
dev-tools project init E:\Projects\MyProjects\some-project --dry-run
dev-tools project init E:\Projects\MyProjects\some-project
```

An existing root `mise.toml` or `.mise.toml` is preserved byte-for-byte. Equal-priority version
conflicts stop generation and are reported.

### 3. Install project versions

```powershell
dev-tools project prepare E:\Projects\MyProjects\some-project --dry-run
dev-tools project prepare E:\Projects\MyProjects\some-project
```

`prepare` requires an existing root `mise.toml` or `.mise.toml`. It explicitly runs
`mise install` in the target project context and never installs repository-wide global defaults.

## Recognized declarations

- mise: `mise.toml`, `.mise.toml`, and `.tool-versions`.
- Java: `.java-version`, `.sdkmanrc`, Maven POM files, and Gradle toolchains.
- Maven: Maven Wrapper; no duplicate Maven declaration is generated when a wrapper exists.
- Node.js: `.nvmrc`, `.node-version`, and `package.json` engines, devEngines, or Volta.
- Package managers: npm, pnpm, and Yarn versions from `packageManager`, engines, or Volta.
- Python: `.python-version`, `pyproject.toml`, `uv.lock`, and uv version requirements.

The scanner parses known text, TOML, JSON, and XML only. Version ranges become reviewable broad
versions, such as `>=3.11` to `python = "3.11"`.

## Working with wsl-devctl

`dev-tools` discovers, generates, and explicitly installs project tool versions.
[`wsl-devctl`](https://github.com/hhhxxxddd/wsl-devctl) mirrors Windows source into WSL ext4 and
manages builds, systemd processes, and live reload.

```bash
dev-tools project init /mnt/e/Projects/CompanyProjects/order-service
wsl-devctl init /mnt/e/Projects/CompanyProjects/order-service \
  --toolchain mise --fix --start
```

`wsl-devctl` can also invoke `dev-tools` while registering a project:

```bash
wsl-devctl init /mnt/e/Projects/CompanyProjects/order-service \
  --toolchain mise --generate-mise --fix --start
```

## Upgrade, uninstall, and tests

After updating the repository, rerun the relevant installer to refresh the entrypoint. Remove
entrypoints with `scripts/uninstall.ps1` or `scripts/uninstall.sh`; installed project runtimes
and caches are preserved.

```powershell
$env:PYTHONPATH = "$PWD\src"
$python = Join-Path "$(mise where python@3.11)" python.exe
& $python -m unittest discover -s tests -t . -v
```

## License

Licensed under the [MIT License](LICENSE).
