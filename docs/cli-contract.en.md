# CLI and automation contract

[简体中文](cli-contract.md) · [Configuration and operation](usage.en.md) · [Project declarations](project-config.en.md)

This contract applies from 0.5.0. Commands, exit codes and JSON outputs are public interfaces. Python modules, worker entrypoints and local state files are not APIs for third-party callers.

## Commands and output

The environment option is `-e win|wsl`, defaulting to the current platform. Project names may be omitted only when the current directory uniquely matches a registered source or workspace; ambiguous matches are never guessed. `list --all` cannot be combined with an explicit environment option.

`--json` selects output format only. `scan` reads declarations; `check` inspects the complete preparation plan read-only; `init` creates missing configuration; `prepare` and `start` execute preparation. There is no public `--dry-run`.

Commands supporting JSON write one UTF-8 JSON object to stdout. Progress and third-party output use stderr. Capture streams separately; do not parse `2>&1` merged output as JSON. Cross-platform calls also validate a single JSON object from the target. Long-running preparation does not use the list query's 30-second timeout.

`logs` emits raw logs and has no JSON mode. Arguments after `--` are not interpreted as controller options. `config edit --json`, `self update --json` and `self upgrade --json` are argument errors. Language comes only from local configuration, defaulting to Chinese. Keys, error codes, state identifiers and numbers do not change with language. Descriptions, diagnostics and third-party text are unsuitable for machine matching.

## Exit codes

| Exit code | Meaning |
|---|---|
| `0` | Query or execution completed; does not imply every service is ready or no software updates exist |
| `1` | Runtime failure, invalid configuration, blocked scan/initialization/preparation check, or partial query results |
| `2` | Usage or argument error, including unknown commands, invalid environments and invalid option combinations |
| `130` | User interruption |

Unresolved `scan`, `init` and `check` results exit `1`. Successful `status` queries exit `0` even when `ready=false`. `list --all` retains other results and exits `1` when an environment query fails; an individual project's `error` does not mean the registration list query failed. Queries of missing states, such as an undeployed WSL backend in `self status`, can also succeed and exit `0`.

`report` retains its complete payload. It exits `1` for explicit collector states `failed`, `timeout`, `partial`, `parse-error`, any `discovery_issues`, or repositories explicitly recording fetch failure, `worktree=error` or `tracking=tracking-error`. Optional tools marked `missing`, disabled entries marked `skipped`, and normal update findings marked `updates` do not themselves make the exit code nonzero.

## Format versions

| Interface | Current version | Meaning |
|---|---|---|
| Project JSON and common error documents | `schema_version=3` | Public CLI format version, independent of the tool release version |
| Local registration JSON | `schema_version=3` | Binding storage format; changing the CLI version does not automatically upgrade or invalidate it |
| `dev-tools.toml` | `schema=1` | Project declaration format; must be the integer `1` |
| Local `config.toml` | No version field | Known fields, types and ranges are strictly validated |

`dev-tools --version` reports the tool release. Internal workers, preparation fingerprints, recovery progress and cache records are not supported for external editing or long-term parsing. This change preserves registration storage version numbers and does not migrate registrations.

Successful `config`, `sysinfo`, `report` and `self status` outputs retain their existing command-specific shapes without imposing the project `schema_version`. Parse fields according to the command invoked. Ignore unknown fields and do not depend on key order, indentation, unspecified array order or human wording. New fields may extend the format; removing fields or changing existing types or meanings requires an explicit format-version and contract update.

## Main result fields

Project JSON uses `environment=windows|wsl`. `list --all` uses top-level `all`, retaining each project's environment. CLI `win` does not change the existing JSON identifier `windows`. Local paths are native to the corresponding platform.

| Command | Main fields and meanings |
|---|---|
| `scan` | `action=scan`; `tools` maps tool names to version evidence; `conflicts/unresolved/diagnostics` are arrays |
| `init` | `action` retains the mise configuration outcome, such as `created/preserved/unresolved`; `project_config.action/path` separately describe project configuration without renaming the top-level action to `init` |
| `check` | `action=check`; boolean `ready` means the plan has no unresolved items; `unresolved/tasks/resolution_tasks` are arrays; this does not imply installation or startup |
| `prepare/start/stop/restart/build/unregister/rename` | Successful results contain the corresponding `action`, `name` and `completed=true`; rename uses the new registration name |
| `register/show` | Include `binding`; show also includes the shared declaration `project` |
| `status` | Boolean `prepared/ready`; boolean-or-null `healthy`; object `services/workers`; diagnostic-or-null `recovery/maintenance_error` |
| `list` | Array `projects` contains statuses or individual project error records; `--all` additionally includes boolean `complete` and array `errors` |

`status.ready` includes a valid preparation fingerprint, ready services, running monitors and no pending recovery. `healthy=null` means at least one service has unknown health; otherwise the current aggregate equals project readiness. Read `services.<name>.health` for an individual probe result: `healthy|unhealthy|unknown`. Service `phase` is `starting|running|restarting|stopped|failed`; `pid/exit_code` may be null. Recovery and error details are diagnostic data; do not depend on human template text.

Main auxiliary fields: config provides `path/state/settings/origins/errors`, with state `loaded|missing|invalid`; sysinfo provides `system/config_state/sections/directories/tools/commands`; report provides `collected_at/platform/repositories/discovery_issues/collectors`; self status provides `controller_version/wsl`, with deployment state `installed|missing|unavailable`.

## Error documents

Runtime errors, argument errors and configuration errors occurring before parsing emit this shape when JSON was explicitly requested. `action` is null when the command cannot be identified; otherwise it is the top-level command name, such as `self`. `message` is safe localized human text. It does not serialize complete command arguments, arbitrary exception text or exception stdout/stderr.

```json
{
  "schema_version": 3,
  "action": "status",
  "error": {
    "code": "project_not_registered",
    "message": "Project is not registered; run dev-tools register",
    "details": {}
  }
}
```

| error.code | Meaning | Optional details |
|---|---|---|
| `invalid_arguments` | Invalid command usage or arguments | None |
| `invalid_config` | Invalid local configuration or recognized TOML configuration | None |
| `project_not_registered` | Specified project is unregistered, or the current directory matches no registration | None |
| `timeout` | Subprocess, read-only query or service-readiness timeout | Numeric `timeout_seconds`, when known |
| `preparation_failed` | Failed prepare/start project operation | Integer `returncode`, when known |
| `operation_failed` | Other runtime error or invalid JSON from the target environment | Integer `returncode`, when known |
| `target_unavailable` | Missing controller or failed connection check | `environment/controller_version/target_version/returncode` (when known) |
| `target_version_mismatch` | Incompatible execution contract | `environment/controller_version/target_version/returncode` (when known) |
| `target_command_unsupported` | Unsupported command or parameter in target version | `environment/controller_version/target_version/returncode` (when known) |
| `target_response_invalid` | Invalid target JSON or protocol | `environment/controller_version/target_version/returncode` (when known) |
| `interrupted` | User interruption | None |

`details` is always an object and is `{}` when no safe facts are available. Scripts should branch on code rather than parse message. Raw third-party progress can still appear on stderr and should be handled as logs.

Blocked scans, initialization and checks, and partial lists/reports retain their diagnostic payloads instead of becoming error documents. Invalid `config --json` / `config check --json` outputs retain configuration fields and add `schema_version/action/error`. A nonzero exit code therefore does not imply an error-only result. Handle the command's payload and inspect error fields, `ready`, `complete` or collector statuses as appropriate.

The source checkout's `tests/test_cli_contract.py` centrally verifies these contracts. Functional tests separately cover execution and read-only boundaries.

`list` defaults to the same query as `list --all`; explicit `-e` limits the query to one target. Project entries add integer-array `ports` and string-array `types`, derived from explicit health checks, root mise and task/service tool declarations. Unknown values use empty arrays and display `-`.

`start --all` operates only on the current or selected environment and rejects a project name or `--service`. JSON includes `projects` and boolean `complete`; entries contain `name/status` (`started|skipped|failed`). Individual failures do not prevent later projects from starting, and return overall exit `1`. Projects with active workers are skipped without preparation or restart.
