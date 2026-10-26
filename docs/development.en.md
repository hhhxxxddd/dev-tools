# Development and verification

[中文](development.md) · [Back to README](../README.en.md)

## Structure and verification

```text
src/dev_tools/
  cli.py, command_help.py                  Command definitions and localized help
  settings.py, i18n.py, locales/           Controller preferences and translations
  scanner.py, metadata.py, versions.py     Static metadata and version discovery
  inspection.py, constraints.py           Project scopes, per-operation metadata snapshots and conservative constraints
  frameworks.py                           Static framework scope conventions
  project_models.py, workflows.py          Scan models, initialization and mise isolation
  ssh.py, ssh_config.py, hosts*.py          SSH and Windows hosts utilities
  toolbox/                                Port, PATH and network diagnostics with native collectors
  projects/                               Declarations, plans, execution, drivers, lifecycle and workers
    lifecycle.py                          Service selection, startup outcomes, rollback scope and recovery policy
  runtimes/router.py                      Cross-environment command and path transport
  runtimes/platforms/                     Native storage, users, sync, processes and systemd
scripts/                                  Installation, entrypoints and process adapters
systemd/                                  Generic worker template
```

Python has no third-party runtime dependencies and supports `>=3.14.8,<3.15`. Use native Python and add src to PYTHONPATH for development checks.

The framework module suggests related directories and marker files from metadata names without reading files or executing project code. Inspection applies path safety, exclusions, scope expansion and snapshot caching. Keep new framework conventions in frameworks.py; the common inventory contains no framework directory names.

The project engine coordinates preparation, synchronization, monitors and state persistence. lifecycle.py owns dependency selection, startup outcomes and recovery policy, using platform adapters for process operations. Dependencies of required services must also become ready. Optional failures are recorded separately: explicit startup reports failure, while background recovery preserves shared services. Fatal failures roll back only workers newly started by the current attempt and retain both the original error and any cleanup error. Maintenance restores services from active workers and startup intent; explicit stop cancels recovery. Application data, tokens, sessions and sidecar ownership stay outside these modules.

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

Lifecycle tests wait for idle backoff before checking edit wakeup and application PIDs. WSL also verifies synchronization neither stops nor stays at its fast interval. When the checkout is on a Windows-mounted drive, place test sources there while registrations, state and caches remain in native Linux temporary directories:

```bash
mkdir -p dist/integration-sources
export DEV_TOOLS_TEST_SOURCE_ROOT="$PWD/dist/integration-sources"
python3 tests/integration/lifecycle_smoke.py
python3 tests/integration/dependency_smoke.py
unset DEV_TOOLS_TEST_SOURCE_ROOT
```

Monitor regressions cover expiration, start-session isolation, pre-build Git revalidation, stop cancellation and snapshot handover. Access regressions cover accepting-but-silent HTTP, IPv4/IPv6, unprivileged queries for root-owned projects and skipped symlink/FIFO metadata.

[MIT License](../LICENSE)

Run `tests/integration/root_windows_transport.py` as WSL root. It isolates the controller and Windows registrations, state and caches, verifying version/SSH/hosts queries plus registration, rename and unregister after sudo clears Windows PATH. The checkout must be on a mounted Windows drive. It reuses installed native Python and does not change system hosts or business registrations.

`tests/integration/environment_smoke.py` runs on Windows and checks native defaults, explicit --all, bidirectional config/sysinfo/report/ssh/self, target-side editors and configuration isolation, Unicode output paths and isolated recovery records. It uses temporary preferences, registrations and state, without updating business projects or writing system hosts. `controller_update_smoke.py` verifies N leaves workers unchanged and Y redeploys and restores the original running subset on each platform; Windows uses an isolated Scoop directory to avoid editing the real profile.

`tests/integration/ssh_smoke.py` uses isolated Win/WSL controllers, SSH configuration, editors and interactive clients to verify real bidirectional process chains, editing of Include source files, stdin and native session exit codes. It does not read real SSH configuration, use keys or connect to external servers.

`tests/integration/ssh_smoke.py` also exercises native/bidirectional SSH list saves and cancellation, plus native/WSL calls to the Windows hosts list. Writes affect temporary files only. `tests/integration/list_terminal_smoke.py` uses a real native WSL PTY and an isolated home to verify field editing, saving, Esc cancellation, Ctrl+C exit and terminal mode restoration. Unit tests additionally cover navigation, validation, provenance, concurrent writes, original-byte backups and failed multi-file rollback.

`tests/integration/project_logs_smoke.py` uses isolated controllers, bindings and logs to verify native Win/WSL and bidirectional aggregation, raw single-service output, task logs, newly created files, live output and interrupt status 130. It does not stop business projects or modify deployed controllers.

## Performance and boundaries

Ruff also checks import ordering, common mistakes and modern Python syntax. Scanning and monitoring skip symlinks, Windows directory junctions and nonregular metadata files. Tests cover malformed declarations, isolated registration failures, unborn Git repositories, detached HEAD, packed refs and worktrees.

Windows port collection calls IP Helper and Toolhelp APIs without third-party dependencies, filtering ports and protocols before reading owners and ancestors. WSL reads process details only for matching sockets, skipping PID and registration scans when no ports match. Discovery walks metadata once. Git monitoring uses one invocation per poll while retaining content fingerprints and in-progress operation checks.

Measured locally on 2026-10-16 with Windows and Python 3.14.8: three runs per command through the same PowerShell entrypoint, reporting median end-to-end time including startup:

| Query | Before | After |
| --- | --- | --- |
| No matching port | 8.58 s | 0.63 s |
| All listening and bound ports | 8.16 s | 1.82 s |

Process counts, caches and system load affect these results; they are not fixed latency guarantees. Native tests verify IPv4/IPv6, TCP/UDP and process identities. Ownership checks are retained and snapshots are not persistently cached.

## CI and releases

`tests/integration/toolbox_smoke.py` uses isolated Windows/WSL controllers and temporary HTTP servers to verify native and bidirectional ports/which/net queries, actual socket PIDs, HEAD responses and query redaction. Business registrations and system network configuration are not modified.

Pushes and Pull Requests run Python 3.14.8 unit/contract tests, Ruff, PowerShell parsing, Bash/ShellCheck, documentation/TOML checks and extracted-package entrypoint checks on Windows and Ubuntu. Run `python -m dev_tools.validation` to check versions, local links, bilingual section structure and TOML. Verification tools are pinned in `requirements-dev.txt`; GitHub Actions use reviewed commit SHAs.

A `vX.Y.Z` tag reuses these checks and builds official assets from a clean matching commit. Windows/Linux asset bytes must agree before publication. Published assets are compared on retries rather than overwritten. Each official release generates a `dev-tools.json` asset for hxd-box to synchronize and maintain its bucket manifest; this repository's workflow does not automatically push bucket updates.

Ubuntu adapter tests do not establish real WSL coverage. Before releasing, run the existing isolated WSL lifecycle/dependency/toolchain scripts and Windows-to-WSL controller/transport scripts, with systemd and installed native runtimes. The controller package check installs twice into temporary native directories and verifies retained bindings, registrations and state. Never use active registrations or middleware data as test fixtures.

The public CLI contract is documented [here](cli-contract.en.md). Candidate checks: `python -m dev_tools.release --output dist`, then `python -m dev_tools.package_smoke dist/dev-tools-0.5.7.zip`. Candidates may describe dirty sources; `--tag v0.5.7 --official` requires committed, clean, correctly tagged sources.
