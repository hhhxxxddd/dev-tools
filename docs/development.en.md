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
