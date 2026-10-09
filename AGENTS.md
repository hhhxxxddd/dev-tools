# dev-tools

This repository owns project discovery, preparation and native Windows/WSL development control.
Pure discovery/workflow modules are separate from platform runtime adapters.

## Boundaries

- Keep Windows and WSL runtime installations and caches platform-native. Share project declarations
  and workflows, not executable directories or global project-runtime defaults. Keep the controller
  host separate from project runtimes; installers must not manage user-global tool declarations.
- Do not embed usernames, drive letters, credentials, proxy settings, or other machine-specific
  values in tracked files. Resolve the repository location from each entrypoint.
- Project scanning may parse known version and build metadata but must never execute project code,
  package scripts, wrappers, or downloaded content.
- Preserve an existing root `mise.toml` or `.mise.toml`. Report equal-priority conflicts instead of
  guessing or overwriting.
- Do not install or upgrade runtimes during `scan` or `init`. Only the explicit
  `prepare` command may install versions, and only from the target project's root mise
  configuration.
- The separate wsl-devctl repository is a business reference, not a runtime dependency.
  Do not import its modules or read/migrate its registrations.
- Route native operations explicitly through `-e win|wsl`; only prepare
  installs runtimes or framework artifacts. Configured branch rebuilds may refresh project packages
  using already prepared runtimes. Keep Unix-only imports inside the WSL adapter.
- Do not infer or install Redis/MySQL or other business services. Use one preparation plan for
  validation and execution, including independent tasks for mixed-language projects.

## Preferences and presentation

- Keep user preferences in native user `config.toml`, shared project workflows in
  `dev-tools.toml`, runtime versions in root mise files, and machine identity in native bindings.
  Do not add parallel JSON preferences, infer project defaults from controller settings, or pass
  a caller's config file into the other platform.
- Resolve language from config before constructing help. Default to Chinese and allow zh/en
  through config only. Keep translations in the central catalogs; translate controller messages
  at presentation boundaries, preserving raw third-party output, JSON keys and status identifiers.
- Config editing uses an existing editor: configured argv, VISUAL, EDITOR, then Notepad/vi.
  WSL sudo calls retain the invoking user's preference scope and editor identity.
- Keep sysinfo read-only: do not execute detected tools or profiles. Report may explicitly refresh
  refs/indexes but must not pull projects, install/upgrade packages, or execute user-defined collectors.

## Documentation

- Keep README.md and README.en.md aligned in sections, commands, defaults and examples.
  Detailed project fields live in docs/project-config.md and docs/project-config.en.md.
- Verify documentation against the command catalog, settings defaults and project parser.
  Check Markdown links and TOML examples; retain Chinese guidance in the default template.
- Deliver referenced documentation and examples with the native WSL deployment. Preserve
  MIT attribution when porting implementation from the business-reference repository.

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

Run the same checks in WSL with native Python and `PYTHONPATH="$PWD/src"`. Do not use Python's
`-P` option for unittest discovery: some tests import the repository's tests package.
Real integration scripts in tests/integration use isolated registrations, state and caches.
WSL lifecycle checks require root and systemd; transport checks start on Windows and require
both entrypoints. Toolchain checks use already-installed versions.

Parse every PowerShell installer before testing bootstrap changes. Bootstrap must use reviewed
package-manager sources and verify a checkout's Git origin before executing its installer.
