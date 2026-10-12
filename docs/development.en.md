# Development and verification

[中文](development.md) · [Back to README](../README.en.md)

## Structure and verification

```text
src/dev_tools/
  cli.py, command_help.py                  Command definitions and localized help
  settings.py, i18n.py, locales/           Controller preferences and translations
  scanner.py, metadata.py, versions.py     Static metadata and version discovery
  project_models.py, workflows.py          Scan models, initialization and mise isolation
  projects/                               Declarations, plans, execution, drivers, lifecycle and workers
  runtimes/router.py                      Cross-environment command and path transport
  runtimes/platforms/                     Native storage, users, sync, processes and systemd
scripts/                                  Installation, entrypoints and process adapters
systemd/                                  Generic worker template
```

Python has no third-party runtime dependencies and supports `>=3.14.8,<3.15`. Use native Python and add src to PYTHONPATH for development checks.

The controller can bind external Python through a native binding (`.host-python.win` on Windows, `.host-python` in WSL). Updates preserve that binding without managing the interpreter's upgrades or removal. Controllers and projects may share a read-only base Python installation while project version declarations, virtual environments and dependency installations remain independent. Isolated installation tests cover explicit paths, repeated installations, startup without mise, platform binding isolation and invalid bindings.

Windows:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m unittest discover -v tests
uvx ruff check src tests
uvx ruff format --check src tests
```

WSL:

```bash
export PYTHONPATH="$PWD/src"
python3 -m unittest discover -v tests
uvx ruff check src tests
uvx ruff format --check src tests
```

Entrypoint changes also require checking `scripts/dev-tools.ps1` and `scripts/dev-tools`. Parse every PowerShell installer before testing bootstrap changes. Maintenance boundaries are in [AGENTS.md](../AGENTS.md).

Real verification scripts use temporary registrations, state and caches: `tests/integration/lifecycle_smoke.py` checks automatic preparation, service additions/removals, HTTP services, hot builds, recovery and rename; `dependency_smoke.py` uses local npm packages to verify package additions, upgrades, removals, lockfile writeback and catching up after stop without downloading third-party packages; `toolchain_smoke.py` uses installed Java/Node/Python/Maven; `transport_smoke.py` checks bidirectional paths, implicit names, help, preferences and language from Windows. WSL lifecycle tests require root and systemd. Toolchain tests add no runtime versions; transport tests require both entrypoints installed.

[MIT License](../LICENSE)

## CI and releases

Pushes and Pull Requests run Python 3.14.8 unit/contract tests, Ruff, PowerShell parsing, Bash/ShellCheck, documentation/TOML checks and extracted-package entrypoint checks on Windows and Ubuntu. Run `python -m dev_tools.validation` to check versions, local links, bilingual section structure and TOML. Verification tools are pinned in `requirements-dev.txt`; GitHub Actions use reviewed commit SHAs.

A `vX.Y.Z` tag reuses these checks and builds official assets from a clean matching commit. Windows/Linux asset bytes must agree before publication. Published assets are compared on retries rather than overwritten. Each official release generates a `dev-tools.json` asset for hxd-box to synchronize and maintain its bucket manifest; this repository's workflow does not automatically push bucket updates.

Ubuntu adapter tests do not establish real WSL coverage. Before releasing, run the existing isolated WSL lifecycle/dependency/toolchain scripts and Windows-to-WSL controller/transport scripts, with systemd and installed native runtimes. The controller package check installs twice into temporary native directories and verifies retained bindings, registrations and state. Never use active registrations or middleware data as test fixtures.

The public CLI contract is documented [here](cli-contract.en.md). Candidate checks: `python -m dev_tools.release --output dist`, then `python -m dev_tools.package_smoke dist/dev-tools-0.5.1.zip`. Candidates may describe dirty sources; `--tag v0.5.1 --official` requires committed, clean, correctly tagged sources.
