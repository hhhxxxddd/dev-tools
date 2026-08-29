# dev-tools

This repository owns the shared mise defaults, cross-platform command wrappers, and deterministic
project toolchain discovery used by the user's Windows and WSL development environments.

## Boundaries

- Keep Windows and WSL runtime installations and caches platform-native. Share declarations and
  workflows, not executable directories.
- Do not embed usernames, drive letters, credentials, proxy settings, or other machine-specific
  values in tracked files. Resolve the repository location from each entrypoint.
- Project scanning may parse known version and build metadata but must never execute project code,
  package scripts, wrappers, or downloaded content.
- Preserve an existing root `mise.toml` or `.mise.toml`. Report equal-priority conflicts instead of
  guessing or overwriting.
- Do not install or upgrade runtimes during `project scan` or `project init`.
- Integrate with `wsl-devctl` through the documented CLI/JSON contract. Do not import either
  project's Python modules into the other project.

## Verification

Run after scanner or CLI changes:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m unittest discover -v tests
uvx ruff check src tests
uvx ruff format --check src tests
```

Also verify `scripts/dev-tools.ps1` on Windows and `scripts/dev-tools` in WSL when changing an
entrypoint or shared mise configuration.

Parse every PowerShell installer before testing bootstrap changes. Bootstrap must use reviewed
package-manager sources and verify a checkout's Git origin before executing its installer.
