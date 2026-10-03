from __future__ import annotations

import json
import os
import platform
import re
import shutil
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
SKIP = {".git", "node_modules", ".venv", "venv", "vendor", "dist", "build", "__pycache__"}


def scrub(value: str) -> str:
    value = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", value)
    value = re.sub(r"(?:https?://|ssh://|git@)[^\s]+", "[remote]", value)
    value = re.sub(
        r"(?i)(token|password|secret|authorization)(\s*[:=]\s*)\S+", r"\1\2[redacted]", value
    )
    home = str(Path.home())
    return value.replace(home, "~")[:24000]


def decode_output(value: bytes) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value.decode("mbcs" if os.name == "nt" else "utf-8", errors="replace")


class Runner:
    def __init__(self, timeout: int = 90):
        self.timeout = timeout

    def run(self, command: list[str], *, cwd: Path | None = None) -> dict:
        started = time.monotonic()
        executable = shutil.which(command[0])
        if not executable and os.name == "nt":
            executable = shutil.which(command[0] + ".ps1")
        if not executable:
            return {"status": "missing", "output": f"{command[0]} unavailable", "seconds": 0}
        command = [executable, *command[1:]]
        if os.name == "nt" and executable.lower().endswith((".ps1", ".cmd", ".bat")):
            # PowerShell argument passing keeps package names and paths as data.
            quoted = " ".join("'" + x.replace("'", "''") + "'" for x in command)
            command = [
                "pwsh",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"& {quoted}; exit $LASTEXITCODE",
            ]
        env = os.environ.copy()
        env.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes -oStrictHostKeyChecking=yes")
        env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", NO_COLOR="1", CI="1")
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
            )
            try:
                output, _ = process.communicate(timeout=self.timeout)
                status = "ok" if process.returncode == 0 else "failed"
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        check=False,
                    )
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                output, _ = process.communicate()
                status = "timeout"
            return {
                "status": status,
                "exit_code": process.returncode,
                "output": scrub(decode_output(output)),
                "seconds": round(time.monotonic() - started, 2),
            }
        except OSError as exc:
            return {"status": "failed", "output": scrub(str(exc)), "seconds": 0}


def discover(roots: list[str], depth: int, limit: int = 256) -> tuple[list[Path], list[dict]]:
    repos, issues, seen = [], [], set()
    visited = 0
    for root in roots:
        path = Path(root).expanduser()
        if not path.is_dir():
            issues.append({"root": scrub(root), "status": "invalid-path"})
            continue
        stack = [(path, 0)]
        while stack:
            current, level = stack.pop()
            key = str(current.resolve())
            if key in seen:
                continue
            seen.add(key)
            visited += 1
            if visited > 10000 or len(repos) >= limit:
                issues.append({"status": "scan-limit", "root": scrub(root)})
                return repos, issues
            if (current / ".git").exists():
                repos.append(current)
                continue
            if level >= depth:
                continue
            try:
                stack.extend(
                    (p, level + 1)
                    for p in current.iterdir()
                    if p.name not in SKIP
                    and not p.is_symlink()
                    and not (getattr(p.lstat(), "st_file_attributes", 0) & 0x400)
                    and p.is_dir()
                )
            except OSError:
                issues.append({"root": scrub(str(current)), "status": "unreadable"})
    return repos, issues


def git_report(path: Path, runner: Runner, refresh: bool) -> dict:
    def git(*args: str) -> dict:
        return runner.run(
            [
                "git",
                "--no-optional-locks",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "maintenance.auto=false",
                "-c",
                "gc.auto=0",
                *args,
            ],
            cwd=path,
        )

    fetch = git("fetch", "--all", "--no-recurse-submodules") if refresh else {"status": "skipped"}
    worktree = git("status", "--porcelain=v1")
    upstream = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    tracking, ahead, behind = "no-upstream", None, None
    if upstream["status"] == "ok":
        counts = git("rev-list", "--left-right", "--count", "HEAD...@{u}")
        match = re.fullmatch(r"\s*(\d+)\s+(\d+)\s*", counts["output"])
        tracking = "tracking-error"
        if counts["status"] == "ok" and match:
            ahead, behind = map(int, match.groups())
            tracking = (
                "diverged"
                if ahead and behind
                else "ahead"
                if ahead
                else "behind"
                if behind
                else "even"
            )
    dirty = len(worktree["output"].splitlines()) if worktree["status"] == "ok" else None
    return {
        "name": path.name,
        "fetch": fetch,
        "freshness": "refreshed" if fetch["status"] == "ok" else "cached",
        "worktree": "error" if dirty is None else "dirty" if dirty else "clean",
        "dirty_entries": dirty,
        "tracking": tracking,
        "ahead": ahead,
        "behind": behind,
    }


def load_config(config: str | None) -> tuple[dict, str]:
    path = Path(
        config or os.environ.get("DEV_TOOLS_REPORT_CONFIG", BASE / "config/report.local.json")
    )
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise TypeError("configuration must be an object")
        if not isinstance(value.get("roots", {}), dict):
            raise TypeError("roots must be an object")
        if not isinstance(value.get("distro", "Ubuntu"), str):
            raise TypeError("distro must be a name")
        roots = value.get("roots", {}).get("windows" if os.name == "nt" else "wsl", [])
        if not isinstance(roots, list) or any(not isinstance(p, str) for p in roots):
            raise ValueError("roots must be a list of paths")
        depth = value.get("max_depth", 2)
        if not isinstance(depth, int) or not 0 <= depth <= 5:
            raise ValueError("max_depth must be between 0 and 5")
        return value, "loaded"
    except FileNotFoundError:
        return {}, "missing"
    except OSError, ValueError, TypeError:
        return {}, "invalid"


def collect_report(
    config: str | None = None,
    *,
    refresh: bool = True,
    timeout: int = 90,
    runner: Runner | None = None,
) -> dict:
    runner = runner or Runner(timeout)
    cfg, config_state = load_config(config)
    roots = cfg.get("roots", {}).get("windows" if os.name == "nt" else "wsl", [])
    repos, issues = discover(roots, cfg.get("max_depth", 2))
    collectors = {}

    def run(name: str, command: list[str]) -> dict:
        result = runner.run(command)
        collectors[name] = result
        return result

    def skip(name: str, message: str) -> None:
        collectors[name] = {"status": "skipped", "output": message}

    repository_results = [git_report(p, runner, refresh) for p in repos]
    installed = run("mise_installed", ["mise", "ls", "--installed", "--json"])
    wsl_installed = None
    if os.name == "nt":
        wrapper = str(BASE / "scripts/dev-tools.ps1")
        run(
            "dev_tools_status",
            [
                "pwsh",
                "-NoProfile",
                "-File",
                wrapper,
                "status",
                "-Distro",
                cfg.get("distro", "Ubuntu"),
            ],
        )
        run("cli_outdated", ["pwsh", "-NoProfile", "-File", wrapper, "cli", "outdated"])
        if refresh:
            run(
                "winget_upgrades",
                ["winget", "upgrade", "--include-unknown", "--disable-interactivity"],
            )
            scoop_refresh = run("scoop_refresh", ["scoop", "update"])
        else:
            skip("winget_upgrades", "not queried: sources may refresh automatically")
            scoop_refresh = {"status": "skipped"}
            skip("scoop_refresh", "refresh disabled")
        scoop = run("scoop_status", ["scoop", "status"])
        scoop["freshness"] = "refreshed" if scoop_refresh["status"] == "ok" else "cached"
        wsl = ["wsl.exe", "-d", cfg.get("distro", "Ubuntu"), "--exec"]
        wsl_installed = run("wsl_mise_installed", [*wsl, "mise", "ls", "--installed", "--json"])
    else:
        run("dev_tools_status", ["bash", str(BASE / "scripts/dev-tools"), "status"])
        skip("windows_tools", "Windows operator collectors require Windows entrypoint")
        wsl = []
    if refresh:
        # Fixed reviewed shell expression; no user-supplied shell fragments or interactive sudo.
        apt_refresh = run(
            "apt_refresh",
            [
                *wsl,
                "sh",
                "-c",
                'if [ "$(id -u)" = 0 ]; then apt-get update; else sudo -n apt-get update; fi',
            ],
        )
    else:
        apt_refresh = {"status": "skipped"}
        skip("apt_refresh", "refresh disabled")
    if apt_refresh["status"] == "ok" and any(
        x in apt_refresh.get("output", "") for x in ("Failed to fetch", "Some index files failed")
    ):
        apt_refresh["status"] = "partial"
    apt = run("apt_upgradable", [*wsl, "env", "LC_ALL=C", "apt", "list", "--upgradable"])
    apt["freshness"] = "refreshed" if apt_refresh["status"] == "ok" else "cached"

    def npm_probe(prefix: list[str], installed_result: dict, label: str = "") -> None:
        npm = run(label + "npm_inventory", [*prefix, "npm", "list", "-g", "--depth=0", "--json"])
        try:
            managed = (
                set(json.loads(installed_result["output"]))
                if installed_result["status"] == "ok"
                else None
            )
            packages = (
                json.loads(npm["output"]).get("dependencies", {}) if npm["status"] == "ok" else None
            )
            if managed is None or packages is None:
                skip(
                    label + "npm_outdated",
                    "cannot establish packages outside mise; no broad outdated query",
                )
                return
            extra = sorted(
                name
                for name in packages
                if name not in {"npm", "corepack"} and f"npm:{name}" not in managed
            )
            if not all(re.fullmatch(r"(?:@[\w.-]+/)?[\w][\w.-]*", name) for name in extra):
                skip(label + "npm_outdated", "invalid package names; query skipped")
            elif extra:
                outdated = run(
                    label + "npm_outdated",
                    [*prefix, "npm", "outdated", "-g", "--depth=0", "--json", *extra],
                )
                if outdated.get("exit_code") == 1:
                    try:
                        if isinstance(json.loads(outdated["output"]), dict) and json.loads(
                            outdated["output"]
                        ):
                            outdated["status"] = "updates"
                    except ValueError:
                        pass
            else:
                skip(label + "npm_outdated", "no extra global npm packages outside mise")
        except ValueError, TypeError, AttributeError:
            skip(label + "npm_outdated", "inventory parse failed")

    npm_probe([], installed)
    if wsl_installed is not None:
        npm_probe(wsl, wsl_installed, "wsl_")
    apt["updates"] = [
        {"name": match.group(1), "available": match.group(2), "installed": match.group(3)}
        for match in re.finditer(
            r"^([^/\s]+)/\S+\s+(\S+)\s+\S+\s+\[upgradable from: ([^\]]+)\]",
            apt.get("output", ""),
            re.MULTILINE,
        )
    ]
    winget = collectors.get("winget_upgrades", {})
    if any(word in winget.get("output", "").lower() for word in ("pinned", "包钉", "pin")):
        winget["coverage_note"] = "Pinned packages excluded by default; no pins changed"
    # Retain version facts, not runtime paths or package-manager private metadata.
    for name in ("mise_installed", "wsl_mise_installed"):
        result = collectors.get(name, {})
        if result.get("status") == "ok":
            try:
                entries = json.loads(result["output"])
                result["versions"] = [
                    {"name": tool, "version": item.get("version"), "active": item.get("active")}
                    for tool, versions in entries.items()
                    for item in versions
                ]
                result["output"] = f"{len(result['versions'])} installed version records"
            except ValueError, TypeError, AttributeError:
                result["status"] = "parse-error"
                result["output"] = "Cannot parse installed versions"
    for name in ("npm_inventory", "wsl_npm_inventory"):
        npm = collectors.get(name, {})
        if npm.get("status") == "ok":
            try:
                npm["versions"] = {
                    name: item.get("version")
                    for name, item in json.loads(npm["output"]).get("dependencies", {}).items()
                }
                npm["output"] = f"{len(npm['versions'])} global packages"
            except ValueError, TypeError, AttributeError:
                npm["output"] = "Cannot parse global packages"
    return {
        "collected_at": datetime.now(UTC).isoformat(),
        "platform": platform.system(),
        "refresh_requested": refresh,
        "config_state": config_state,
        "roots_count": len(roots),
        "repositories": repository_results,
        "discovery_issues": issues,
        "collectors": collectors,
        "policy": "fetch and indexes only; no pull, install, upgrade, agreements or credential changes",
    }


def render_report(report: dict) -> str:
    lines = [
        f"日报 {report['collected_at']} ({report['platform']})",
        f"配置: {report['config_state']}；仓库: {len(report['repositories'])}；刷新: {report['refresh_requested']}",
    ]
    for repo in report["repositories"]:
        lines.append(
            f"{repo['name']}: {repo['worktree']} ({repo['dirty_entries']}) / {repo['tracking']} +{repo['ahead']}/-{repo['behind']} / fetch {repo['fetch']['status']} / {repo['freshness']}"
        )
    for issue in report["discovery_issues"]:
        lines.append(f"目录异常: {issue['status']} {issue.get('root', '')}")
    for name, result in report["collectors"].items():
        lines.append(
            f"\n{name}: {result['status']} {result.get('freshness', '')}\n{result.get('output', '')[:4000]}"
        )
    return "\n".join(lines)
