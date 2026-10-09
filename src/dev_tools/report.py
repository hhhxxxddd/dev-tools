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

from .i18n import language_scope, t
from .presentation import label as display_label
from .report_updates import (
    mise_updates,
    query_updates,
    rustup_updates,
    snap_packages,
    store_packages,
)
from .settings import Settings, load_settings, native_path

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

    def run(self, command: list[str], *, cwd: Path | None = None, input: str | None = None) -> dict:
        started = time.monotonic()
        executable = shutil.which(command[0])
        if not executable and os.name == "nt":
            executable = shutil.which(command[0] + ".ps1")
        if not executable:
            return {
                "status": "missing",
                "output": t("工具不可用：{command}", command=command[0]),
                "seconds": 0,
            }
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
                stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
            )
            try:
                output, _ = process.communicate(
                    input=input.encode() if input is not None else None, timeout=self.timeout
                )
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


def collect_report(
    config: str | Path | None = None,
    *,
    refresh: bool | None = None,
    timeout: int | None = None,
    runner: Runner | None = None,
    settings: Settings | None = None,
) -> dict:
    settings = settings or load_settings(config)
    with language_scope(settings.language):
        return _collect_report(settings, refresh=refresh, timeout=timeout, runner=runner)


def _collect_report(
    settings: Settings, *, refresh: bool | None, timeout: int | None, runner: Runner | None
) -> dict:
    cfg = settings.data["report"]
    refresh = cfg["refresh"] if refresh is None else refresh
    timeout = cfg["timeout"] if timeout is None else timeout
    runner = runner or Runner(timeout)
    enabled = set(cfg["collectors"])
    roots = [str(native_path(path, settings.path.parent)) for path in cfg["roots"]]
    repos, issues = discover(roots, cfg["max_depth"]) if "git" in enabled else ([], [])
    collectors = {}

    def run(name: str, command: list[str], **kwargs) -> dict:
        result = runner.run(command, **kwargs)
        collectors[name] = result
        return result

    def skip(name: str, message: str) -> None:
        collectors[name] = {"status": "skipped", "output": message}

    repository_results = [git_report(p, runner, refresh) for p in repos]
    installed = {"status": "skipped"}
    if "mise" in enabled:
        installed = run("mise_installed", ["mise", "ls", "--installed", "--json"])
        run("mise_version", ["mise", "--version"])
        if refresh:
            outdated = run("mise_outdated", ["mise", "outdated", "--json"])
            query_updates(outdated, mise_updates)
        else:
            skip("mise_outdated", t("已禁用刷新"))
    wsl_installed = None
    native_wsl = os.name != "nt"
    wsl = [] if native_wsl else ["wsl.exe", "-d", settings.distro, "--exec"]
    if os.name == "nt":
        if "winget" in enabled:
            if refresh:
                run(
                    "winget_upgrades",
                    ["winget", "upgrade", "--include-unknown", "--disable-interactivity"],
                )
            else:
                skip("winget_upgrades", t("未查询：软件源可能自动刷新"))
        if "store" in enabled:
            if refresh:
                # Supply only a negative answer; never use --apply. Some CLI
                # versions reject redirected input after completing the query.
                store = run("store_updates", ["store", "updates"], input="n\n")
                query_updates(
                    store,
                    lambda output: [{**item, "available": None} for item in store_packages(output)],
                )
                count = re.search(r"Updates available\s*\((\d+) found\)", store.get("output", ""))
                if (
                    count
                    and store.get("updates") is not None
                    and len(store["updates"]) != int(count[1])
                ):
                    store["status"] = "partial"
                if (
                    "Failed to read input in non-interactive mode" in store.get("output", "")
                    and store.get("updates") is not None
                ):
                    store["status"] = "partial"
                store["coverage_note"] = t("Store CLI 查询全部已安装应用；未提供目标版本时记为未知")
            else:
                skip("store_updates", t("已禁用刷新"))
        if "scoop" in enabled:
            if refresh:
                scoop_refresh = run("scoop_refresh", ["scoop", "update"])
            else:
                scoop_refresh = {"status": "skipped"}
                skip("scoop_refresh", t("已禁用刷新"))
            scoop = run("scoop_status", ["scoop", "status"])
            scoop["freshness"] = "refreshed" if scoop_refresh["status"] == "ok" else "cached"
        if "wsl" in enabled and "mise" in enabled:
            run("wsl_mise_version", [*wsl, "mise", "--version"])
            wsl_installed = run("wsl_mise_installed", [*wsl, "mise", "ls", "--installed", "--json"])
            if refresh:
                outdated = run("wsl_mise_outdated", [*wsl, "mise", "outdated", "--json"])
                query_updates(outdated, mise_updates)
            else:
                skip("wsl_mise_outdated", t("已禁用刷新"))
    elif enabled.intersection({"winget", "scoop", "store"}):
        skip("windows_tools", t("Windows 工具信息需要从 Windows 入口收集"))
    apt = {}
    if "apt" in enabled and (native_wsl or "wsl" in enabled):
        if refresh:
            # Reviewed fixed expression; no user-supplied hooks or interactive sudo.
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
            skip("apt_refresh", t("已禁用刷新"))
        if apt_refresh["status"] == "ok" and any(
            x in apt_refresh.get("output", "")
            for x in ("Failed to fetch", "Some index files failed")
        ):
            apt_refresh["status"] = "partial"
        apt = run("apt_upgradable", [*wsl, "env", "LC_ALL=C", "apt", "list", "--upgradable"])
        apt["freshness"] = "refreshed" if apt_refresh["status"] == "ok" else "cached"

    if "snap" in enabled and (native_wsl or "wsl" in enabled):
        inventory = run("snap_inventory", [*wsl, "env", "LC_ALL=C", "snap", "list"])
        if inventory.get("exit_code") == 127:
            inventory["status"] = "missing"
        if inventory["status"] == "ok":
            try:
                inventory["versions"] = snap_packages(inventory["output"])
            except ValueError:
                inventory["status"] = "parse-error"
        if refresh:
            snaps = run("snap_updates", [*wsl, "env", "LC_ALL=C", "snap", "refresh", "--list"])
            if snaps.get("exit_code") == 127:
                snaps["status"] = "missing"
            versions = {item["name"]: item for item in inventory.get("versions", [])}
            query_updates(
                snaps,
                lambda output: [
                    {
                        "name": item["name"],
                        "installed": versions.get(item["name"], {}).get("version"),
                        "available": item["version"],
                        "installed_revision": versions.get(item["name"], {}).get("revision"),
                        "available_revision": item["revision"],
                    }
                    for item in snap_packages(output)
                ],
            )
            if inventory["status"] != "ok" and snaps["status"] in {"ok", "updates"}:
                snaps["status"] = "partial"
        else:
            skip("snap_updates", t("已禁用刷新"))

    if "rustup" in enabled:
        if refresh:
            if os.name == "nt":
                # Prefer the native executable rather than a mise activation shim.
                cargo_home = os.environ.get("CARGO_HOME") or os.path.join(
                    os.path.expanduser("~"), ".cargo"
                )
                rustup = type(settings.path)(cargo_home) / "bin" / "rustup.exe"
                command = [str(rustup) if rustup.is_file() else "rustup", "check"]
                rust = run("rustup_updates", command)
                query_updates(rust, rustup_updates, success_codes=(0, 100))
            if native_wsl or "wsl" in enabled:
                # No profiles, Windows executables, or installer are invoked.
                probe = (
                    'tool="$(command -v rustup || true)"; '
                    'case "$tool" in /mnt/[a-z]/*) tool=;; esac; '
                    'if [ -z "$tool" ] && [ -x "${CARGO_HOME:-$HOME/.cargo}/bin/rustup" ]; '
                    'then tool="${CARGO_HOME:-$HOME/.cargo}/bin/rustup"; fi; '
                    'if [ -z "$tool" ]; then echo "rustup: not found" >&2; exit 127; fi; '
                    'exec "$tool" check'
                )
                name = "rustup_updates" if native_wsl else "wsl_rustup_updates"
                rust = run(name, [*wsl, "env", "LC_ALL=C", "sh", "-c", probe])
                if rust.get("exit_code") == 127:
                    rust["status"] = "missing"
                query_updates(rust, rustup_updates, success_codes=(0, 100))
        else:
            skip("rustup_updates", t("已禁用刷新"))
            if os.name == "nt" and "wsl" in enabled:
                skip("wsl_rustup_updates", t("已禁用刷新"))

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
                    t("无法确定未由 mise 管理的包；已跳过更新检查"),
                )
                return
            extra = sorted(
                name
                for name in packages
                if name not in {"npm", "corepack"} and f"npm:{name}" not in managed
            )
            if not all(re.fullmatch(r"(?:@[\w.-]+/)?[\w][\w.-]*", name) for name in extra):
                skip(label + "npm_outdated", t("包名称无效；已跳过查询"))
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
                skip(label + "npm_outdated", t("没有额外的未由 mise 管理的全局 npm 包"))
        except ValueError, TypeError, AttributeError:
            skip(label + "npm_outdated", t("无法解析包清单"))

    if "npm" in enabled:
        npm_probe([], installed)
        if not native_wsl and "wsl" in enabled:
            npm_probe(wsl, wsl_installed or {"status": "skipped"}, "wsl_")
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
        winget["coverage_note"] = t("默认排除已锁定版本的包；版本锁定设置保持原值")
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
                result["output"] = t("已安装版本记录：{value} 条", value=len(result["versions"]))
            except ValueError, TypeError, AttributeError:
                result["status"] = "parse-error"
                result["output"] = t("无法解析已安装版本")
    for name in ("npm_inventory", "wsl_npm_inventory"):
        npm = collectors.get(name, {})
        if npm.get("status") == "ok":
            try:
                npm["versions"] = {
                    name: item.get("version")
                    for name, item in json.loads(npm["output"]).get("dependencies", {}).items()
                }
                npm["output"] = t("全局包：{value} 个", value=len(npm["versions"]))
            except ValueError, TypeError, AttributeError:
                npm["output"] = t("无法解析全局包")
    return {
        "collected_at": datetime.now(UTC).isoformat(),
        "platform": platform.system(),
        "refresh_requested": refresh,
        "config_state": settings.state,
        "roots_count": len(roots),
        "language": settings.language,
        "repositories": repository_results,
        "discovery_issues": issues,
        "collectors": collectors,
        "policy": "fetch and indexes only; no pull, install, upgrade, agreements or credential changes",
    }


def render_report(report: dict) -> str:
    with language_scope(report.get("language", "zh")):
        return _render_report(report)


def _render_report(report: dict) -> str:
    lines = [
        t(
            "日报 {collected_at} ({platform})",
            collected_at=report["collected_at"],
            platform=report["platform"],
        ),
        t(
            "配置：{config_state}；仓库：{value}；刷新：{refresh_requested}",
            config_state=display_label(report["config_state"]),
            value=len(report["repositories"]),
            refresh_requested=display_label(report["refresh_requested"]),
        ),
    ]
    for repo in report["repositories"]:
        lines.append(
            t(
                "{name}：{worktree}（改动条目：{value}）；{tracking} +{value5}/-{value6}；引用刷新：{status}；{freshness}",
                name=repo["name"],
                worktree=display_label(repo["worktree"]),
                value=repo["dirty_entries"] if repo["dirty_entries"] is not None else "未知",
                tracking=display_label(repo["tracking"]),
                value5=repo["ahead"] if repo["ahead"] is not None else "?",
                value6=repo["behind"] if repo["behind"] is not None else "?",
                status=display_label(repo["fetch"]["status"]),
                freshness=display_label(repo["freshness"]),
            )
        )
    for issue in report["discovery_issues"]:
        lines.append(
            t(
                "目录异常：{status} {value}",
                status=display_label(issue["status"]),
                value=issue.get("root", ""),
            )
        )
    titles = {
        "mise_installed": t("mise 已安装工具"),
        "mise_version": t("mise 版本"),
        "mise_outdated": t("mise 可更新工具"),
        "winget_upgrades": t("winget 可更新软件"),
        "store_updates": t("Microsoft Store 可更新应用"),
        "scoop_refresh": t("Scoop 软件索引刷新"),
        "scoop_status": t("Scoop 软件状态"),
        "windows_tools": t("Windows 工具信息"),
        "apt_refresh": t("APT 软件索引刷新"),
        "apt_upgradable": t("APT 可更新软件"),
        "snap_inventory": t("Snap 已安装软件"),
        "snap_updates": t("Snap 可更新软件"),
        "rustup_updates": t("Rustup 可更新工具链"),
        "npm_inventory": t("npm 全局包清单"),
        "npm_outdated": t("npm 可更新全局包"),
    }
    for name, result in report["collectors"].items():
        title = (
            "WSL " + titles.get(name[4:], name[4:])
            if name.startswith("wsl_")
            else titles.get(name, name)
        )
        lines.append(
            f"\n{title}：{display_label(result['status'])} {display_label(result.get('freshness', ''))}\n{result.get('output', '')[:4000]}"
        )
    return "\n".join(lines)
