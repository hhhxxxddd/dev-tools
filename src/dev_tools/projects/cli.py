from __future__ import annotations

import argparse
import json
import os
import subprocess
from urllib.parse import urlsplit

from ..i18n import error_detail, message, render, t
from ..presentation import colored, error, format_table, label, status_label, warning
from ..progress import progress_scope, stage
from ..runtimes.platforms.factory import backend_for
from ..runtimes.platforms.layout import require_registration_control
from ..runtimes.platforms.locking import operation_lock
from ..schemas import CLI_SCHEMA_VERSION
from .config import load_project, parse_project
from .drivers import remove_owned_tree
from .engine import ProjectEngine
from .models import ProjectError, validate_name
from .registry import Registry, read_json


def engine_for(environment: str, name: str, *, allow_stale: bool = False) -> ProjectEngine:
    binding = Registry(environment).load(name)
    try:
        spec = load_project(binding.source, environment)
    except ValueError, OSError:
        if not allow_stale:
            raise
        previous = read_json(binding.state / "project.json").get("last_project")
        spec = parse_project(
            previous or {"schema": 1, "name": name, "toolchain": "system"}, environment
        )
    return ProjectEngine(spec, binding, backend_for(binding))


def list_projects(environment: str, settings=None) -> list[dict]:
    native = "windows" if os.name == "nt" else "wsl"
    if environment != native:
        from ..cli_errors import RemoteError, TargetError
        from ..runtimes.diagnostics import preflight, response_payload
        from ..runtimes.router import forward_remote

        target = "win" if environment == "windows" else environment
        if settings is None:
            from ..settings import load_settings

            settings = load_settings()
        preflight(target, ["list"], settings)
        try:
            result = forward_remote(
                target,
                ["list", "--json"],
                settings=settings,
                capture=True,
            )
        except subprocess.TimeoutExpired:
            raise ProjectError(
                message(
                    "project list query timed out in {environment}", environment=label(environment)
                )
            ) from None
        payload = response_payload(result, target, "list")
        if "error" in payload:
            raise RemoteError(payload, result.returncode)
        try:
            if (
                result.returncode != 0
                or not isinstance(payload, dict)
                or payload.get("schema_version") != CLI_SCHEMA_VERSION
                or payload.get("action") != "list"
                or payload.get("environment") != environment
                or not isinstance(payload.get("projects"), list)
                or any(
                    not isinstance(item, dict)
                    or not isinstance(item.get("name"), str)
                    or item.get("environment", environment) != environment
                    for item in payload["projects"]
                )
            ):
                raise ValueError
        except ValueError:
            raise TargetError(
                "target_response_invalid",
                message("invalid project list from {environment}", environment=label(environment)),
                environment=target,
                returncode=result.returncode,
            ) from None
        projects = [{**item, "environment": environment} for item in payload["projects"]]
        if os.name == "nt" and environment == "wsl":
            from .access import windows_access

            projects = windows_access(projects)
        return projects
    projects = []
    for name in Registry(environment).names():
        try:
            engine = engine_for(environment, name)
            projects.append({**engine.status(), **project_columns(engine)})
        except (ValueError, OSError) as exc:
            projects.append(
                {
                    "name": name,
                    "environment": environment,
                    "ready": False,
                    "error": str(exc),
                    "error_detail": error_detail(exc),
                }
            )
    return projects


def project_columns(engine):
    """Display explicit declarations only; never execute tools or parse shell scripts."""
    from .toolchain import root_runtime_versions

    ports, tools = set(), set()
    for service in engine.spec.services.values():
        if service.health.get("tcp"):
            ports.add(service.health["tcp"])
        if service.health.get("http"):
            url = urlsplit(service.health["http"])
            port = url.port or {"http": 80, "https": 443}.get(url.scheme)
            if port:
                ports.add(port)
        tools.update(service.tools)
        if service.driver == "spring":
            tools.add("java")
    for task in engine.spec.tasks.values():
        tools.update(task.tools)
        tools.add(
            {
                "uv": "python",
                "pip": "python",
                "npm": "node",
                "pnpm": "node",
                "yarn": "node",
                "maven": "java",
            }.get(task.manager, task.manager)
        )
    tools.update(item.rsplit("@", 1)[0] for item in root_runtime_versions(engine.binding.source))
    languages = {"python", "java", "node", "bun", "go", "ruby", "rust", "dotnet", "deno"}
    return {"ports": sorted(ports), "types": sorted(tools & languages)}


def start_all(environment, as_json):
    from ..cli_errors import error_payload

    results = []
    for name in Registry(environment).names():
        try:
            engine = engine_for(environment, name)
            if engine._active():
                results.append({"name": name, "status": "skipped"})
            else:
                with progress_scope(name, enabled=not as_json), stage("Prepare and start project"):
                    engine.start()
                results.append({"name": name, "status": "started"})
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            results.append(
                {
                    "name": name,
                    "status": "failed",
                    "error": error_payload(exc, action="start")["error"],
                    "error_detail": error_detail(exc),
                }
            )
    payload = {
        "schema_version": CLI_SCHEMA_VERSION,
        "action": "start",
        "environment": environment,
        "projects": results,
        "complete": not any(item["status"] == "failed" for item in results),
    }
    if as_json:
        payload["projects"] = [
            {key: value for key, value in item.items() if key != "error_detail"} for item in results
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in results:
            print(
                t(
                    "{project}: {result}",
                    project=item["name"],
                    result=colored(
                        t(
                            {
                                "skipped": "Already running; skipped",
                                "started": "Started",
                                "failed": "Failed",
                            }[item["status"]]
                        ),
                        {"started": "green", "skipped": "yellow", "failed": "red"}[item["status"]],
                    ),
                )
            )
            if item.get("error_detail"):
                print(error(render(item["error_detail"])))
        if not results:
            print(t("  暂无已注册项目。"))
    return 0 if payload["complete"] else 1


def _list_state(project: dict) -> str:
    if project.get("error") or project.get("error_detail"):
        return "error"
    if project.get("recovery") or project.get("maintenance_error"):
        return "recovery-pending"
    services = project.get("services", {}).values()
    phases = {service["phase"] for service in services}
    if not phases or phases == {"stopped"}:
        return "stopped"
    for phase in ("failed", "restarting", "starting"):
        if phase in phases:
            return phase
    if any(service["health"] == "unhealthy" for service in services):
        return "unhealthy"
    access = project.get("windows_access", {}).get("state")
    if access == "unreachable":
        return "access-unavailable"
    if access == "unknown":
        return "access-unknown"
    if phases == {"running"}:
        return "running" if project["ready"] else "not-ready"
    return "partially-running" if "running" in phases else "unknown"


def emit_access(project: dict) -> None:
    access = project.get("windows_access")
    if not access:
        return
    state = access["state"]
    descriptions = {
        "reachable": "Windows localhost access is reachable",
        "unreachable": "Windows localhost access failed; WSL-local health does not prove Windows access",
        "unknown": "Windows access could not be checked; update the WSL controller for access metadata",
        "not-applicable": "No running services with local access checks",
    }
    text = t("{project}: {detail}", project=project["name"], detail=t(descriptions[state]))
    print(warning(text) if state in {"unreachable", "unknown"} else text)
    for check in access["checks"]:
        if not check["reachable"]:
            print(
                warning(
                    t(
                        "  {service}: {target} ({stage}/{code})",
                        service=check["service"],
                        target=check["target"],
                        stage=check["stage"],
                        code=check["code"],
                    )
                )
            )


def remote_wsl_status(arguments, settings, as_json):
    from ..cli_errors import RemoteError, TargetError, exit_code
    from ..runtimes.diagnostics import response_payload
    from ..runtimes.router import forward_remote
    from .access import windows_access

    arguments = list(arguments)
    if "--json" not in arguments:
        index = arguments.index("--") if "--" in arguments else len(arguments)
        arguments.insert(index, "--json")
    result = forward_remote("wsl", arguments, settings=settings, capture=True, timeout=None)
    value = response_payload(result, "wsl", "status")
    if "error" in value:
        raise RemoteError(value, result.returncode)
    if (
        result.returncode != 0
        or value.get("environment") != "wsl"
        or not isinstance(value.get("name"), str)
        or not isinstance(value.get("services"), dict)
        or any(type(value.get(key)) is not bool for key in ("ready", "prepared"))
        or value.get("healthy") is not None
        and type(value["healthy"]) is not bool
        or any(
            not isinstance(service, dict)
            or not all(isinstance(service.get(key), str) for key in ("phase", "health"))
            for service in value.get("services", {}).values()
        )
    ):
        raise TargetError(
            "target_response_invalid",
            message(
                "Target controller returned an invalid response; check both controller versions"
            ),
            environment="wsl",
        )
    emit(windows_access([value])[0], as_json)
    return exit_code(result.returncode)


def emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(
        f"{label(payload['action'])}：{payload.get('name', label(payload.get('environment', '')))}"
    )
    if payload.get("binding"):
        for key, title in (
            ("source", t("源码目录")),
            ("workspace", t("工作目录")),
            ("state", t("状态目录")),
        ):
            print(f"  {title}：{payload['binding'][key]}")
    if "project" in payload:
        from .config import render_toml

        print(t("项目声明（TOML）："))
        print(render_toml(payload["project"]))
    if "services" in payload:
        print(
            t(
                "  准备有效：{prepared}；服务就绪：{ready}；健康检查：{value}",
                prepared=label(payload["prepared"]),
                ready=label(payload["ready"]),
                value=status_label(
                    "unknown"
                    if payload["healthy"] is None
                    else "healthy"
                    if payload["healthy"]
                    else "unhealthy"
                ),
            )
        )
        for name, value in payload["services"].items():
            print(f"  {name}：{status_label(value['phase'])}（{status_label(value['health'])}）")
        emit_access(payload)
        if payload.get("health_scope") == "wsl-local":
            print(t("Health scope: WSL local only; Windows localhost forwarding is not checked"))
        if payload.get("maintenance_error"):
            print(warning(t("自动维护待处理：{error}", error=render(payload["maintenance_error"]))))
        if payload.get("recovery"):
            recovery = payload["recovery"]
            print(
                warning(
                    t("  待恢复操作：{value}", value=label(recovery.get("operation", "unknown")))
                )
            )
            print(
                t("  待恢复服务与监控：")
                + (", ".join(recovery.get("restore_workers", [])) or t("无"))
            )
            if recovery.get("error"):
                print(
                    t(
                        "  恢复原因：{value}",
                        value=render(recovery.get("error_detail") or recovery["error"]),
                    )
                )
    if "projects" in payload:
        print(
            format_table(
                (t("Name"), t("Environment"), t("Ports"), t("Type"), t("State")),
                [
                    (
                        project["name"],
                        label(project.get("environment", payload["environment"])),
                        ", ".join(str(port) for port in project.get("ports", [])) or "-",
                        ", ".join(project.get("types", [])) or "-",
                        status_label(_list_state(project)),
                    )
                    for project in payload["projects"]
                ],
            )
        )
        for project in payload["projects"]:
            if project.get("windows_access", {}).get("state") in {"unreachable", "unknown"}:
                emit_access(project)
            if project.get("error") or project.get("error_detail"):
                print(
                    error(
                        t(
                            "  {name}：{value}",
                            name=project["name"],
                            value=render(project.get("error_detail") or project["error"]),
                        )
                    )
                )
        if any(project.get("health_scope") == "wsl-local" for project in payload["projects"]):
            print(t("Health scope: WSL local only; Windows localhost forwarding is not checked"))
        for diagnostic in payload.get("errors", []):
            print(
                warning(
                    t(
                        "  {environment}：{value}",
                        environment=label(diagnostic["environment"]),
                        value=render(diagnostic.get("error_detail") or diagnostic["error"]),
                    )
                )
            )
        if not payload["projects"] and not payload.get("errors"):
            print(t("  暂无已注册项目。"))
    if "runtime_versions" in payload:
        print(t("  运行时版本：") + (", ".join(payload["runtime_versions"]) or t("无")))
        if payload.get("configuration_update"):
            print(t("  将更新自动发现的项目配置。"))
        for task in payload.get("resolution_tasks", []):
            print(t("  锁文件同步 {name}：{command}", name=task["name"], command=task["command"]))
        for task in payload["tasks"]:
            print(
                t(
                    "  任务 {name}（{cwd}）：{command}",
                    name=task["name"],
                    cwd=task["cwd"],
                    command=task["command"],
                )
            )
        requirements = payload["platform_requirements"]
        print(t("  平台依赖：") + (", ".join(requirements["packages"]) or t("无")))
        print(
            t(
                "  启用 Docker：{enable_docker}；加入 Docker 用户组：{docker_group}",
                enable_docker=label(requirements["enable_docker"]),
                docker_group=label(requirements["docker_group"]),
            )
        )
        for message in payload["unresolved"]:
            print(t("  待解决：") + render(message))
    if payload.get("completed"):
        print(t("  操作已完成。"))


def run(args: argparse.Namespace) -> int:
    action = args.project_command
    environment = "windows" if args.env == "win" else args.env
    payload = {"schema_version": CLI_SCHEMA_VERSION, "action": action, "environment": environment}
    if action == "list":
        settings = getattr(args, "settings", None)
        if not args.all_environments:
            emit({**payload, "projects": list_projects(environment, settings)}, args.json)
            return 0
        projects, errors = [], []
        for target in ("windows", "wsl"):
            try:
                projects.extend(list_projects(target, settings))
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                from ..cli_errors import error_payload

                errors.append(
                    {
                        "environment": target,
                        "error": str(exc),
                        "error_detail": error_detail(exc),
                        "diagnostic": error_payload(exc, action="list")["error"],
                    }
                )
        emit(
            {
                **payload,
                "environment": "all",
                "projects": projects,
                "errors": errors,
                "complete": not errors,
            },
            args.json,
        )
        return 1 if errors else 0
    registry = Registry(environment)
    if action == "start" and getattr(args, "all_projects", False):
        return start_all(environment, args.json)
    if action == "register":
        from pathlib import Path

        require_registration_control(environment)
        binding = registry.register(
            Path(args.path), name=args.name, run_user=args.user, force=args.force
        )
        emit({**payload, "binding": binding.as_dict()}, args.json)
        return 0
    if action == "_worker":
        from .worker import run_worker

        name, separator, worker = args.instance.partition(":")
        if not separator or ":" in worker:
            raise ProjectError(message("worker instance must be PROJECT:SERVICE"))
        validate_name(name)
        if worker not in {"__sync", "__watch"}:
            validate_name(worker)
        return run_worker(engine_for(environment, name), worker)
    args.name = registry.resolve_name(args.name)
    engine = engine_for(
        environment, args.name, allow_stale=action in {"stop", "unregister", "logs"}
    )
    if action in {"check", "prepare"}:
        plan = engine.plan(force=True)
        if action == "check":
            emit({**plan.as_dict(), "action": "check"}, args.json)
            return 1 if plan.unresolved else 0
        engine.prepare(plan)
    elif action == "start":
        with progress_scope(args.name, enabled=not args.json), stage("Prepare and start project"):
            engine.start((args.service,) if args.service else None)
    elif action == "stop":
        engine.stop()
    elif action == "restart":
        with progress_scope(args.name, enabled=not args.json), stage("Restart services"):
            engine.restart()
    elif action == "status":
        emit(engine.status(), args.json)
        return 0
    elif action == "show":
        emit(
            {**payload, "binding": engine.binding.as_dict(), "project": engine.spec.raw}, args.json
        )
        return 0
    elif action == "build":
        engine.rebuild(service=args.service)
    elif action == "logs":
        engine.logs(args.service, lines=args.lines, follow=args.follow, task=args.task)
        return 0
    elif action == "rename":
        validate_name(args.new_name)
        if registry.path(args.new_name).exists():
            raise ProjectError(
                message("project is already registered: {new_name}", new_name=args.new_name)
            )
        engine.backend.require_control()
        with operation_lock(engine.binding.state, timeout=15):
            active = engine._active()
            engine._stop(active)
            registry.rename(args.name, args.new_name)
            renamed = engine_for(environment, args.new_name)
            if active:
                renamed._start(
                    tuple(name for name in active if name in renamed.spec.services),
                    infrastructure=any(name.startswith("__") for name in active),
                )
        payload["name"] = args.new_name
    elif action == "unregister":
        if args.purge and environment == "windows":
            raise ProjectError(message("Windows 工作目录即源码目录，不能使用 --purge 删除"))
        engine.backend.require_control()
        with operation_lock(engine.binding.state, timeout=15):
            engine._stop(
                tuple(dict.fromkeys([*engine.backend.known_workers(), *engine.spec.services]))
            )
            if args.purge and engine.binding.workspace.exists():
                remove_owned_tree(engine.binding.workspace, engine.binding.cache_root)
            registry.path(args.name).unlink()
    emit({**payload, "name": payload.get("name", args.name), "completed": True}, args.json)
    return 0


def add_commands(commands) -> None:
    register = commands.add_parser("register", help=t("注册项目到所选环境"))
    register.add_argument(
        "path", nargs="?", default=".", metavar=t("目录"), help=t("源码目录，默认当前目录")
    )
    register.add_argument(
        "--name", metavar=t("注册名"), help=t("本机注册别名，默认使用项目配置中的名称")
    )
    register.add_argument(
        "--user",
        default="",
        metavar=t("用户名"),
        help=t("WSL 运行用户；省略时使用当前或转发时的发行版默认用户"),
    )
    register.add_argument(
        "--force", action="store_true", help=t("允许重复注册同一源码目录和运行用户；不覆盖其他绑定")
    )
    register.add_argument("--json", action="store_true", help=t("以 JSON 输出结果"))
    register.set_defaults(func=run)
    listing = commands.add_parser(
        "list", help=t("列出当前环境的项目、状态、端口和类型；--all 查询两端")
    )
    listing.add_argument(
        "--all",
        dest="all_environments",
        action="store_true",
        help=t("汇总 Windows 和 WSL 的全部注册项目"),
    )
    listing.add_argument("--json", action="store_true", help=t("以 JSON 输出结果"))
    listing.set_defaults(func=run)
    for action, description in (
        ("check", t("只读校验并查看完整准备计划")),
        ("prepare", t("准备项目运行时、依赖和构建")),
        ("start", t("自动准备并启动项目服务和监控")),
        ("stop", t("停止项目服务和监控")),
        ("restart", t("重启项目服务和监控")),
        ("status", t("查看项目准备情况、服务状态和健康检查")),
        ("build", t("使用已准备的运行时重新构建项目")),
        ("logs", t("查看指定服务的日志")),
        ("unregister", t("停止服务并注销；默认保留源码、缓存和运行时")),
        ("show", t("查看项目声明和本机绑定")),
        ("rename", t("修改注册名称并恢复原先运行的服务")),
    ):
        command = commands.add_parser(
            action,
            help=description,
            group=t("进阶命令")
            if action in {"prepare", "build", "show", "rename"}
            else t("常用命令"),
        )
        command.add_argument(
            "name", nargs="?", metavar=t("项目名"), help=t("注册名称；省略时按当前目录唯一匹配")
        )
        command.set_defaults(func=run)
        if action != "logs":
            command.add_argument(
                "--json",
                action="store_true",
                help=t("以 JSON 输出准备计划") if action == "check" else t("以 JSON 输出结果"),
            )
        if action in {"start", "build"}:
            command.add_argument(
                "--service",
                metavar=t("服务名"),
                help=t("仅操作指定服务；启动时包含其依赖，省略时操作全部服务"),
            )
        if action == "start":
            command.add_argument(
                "--all",
                dest="all_projects",
                action="store_true",
                help=t("启动所选环境的全部项目，跳过已有活动服务的项目"),
            )
        if action == "logs":
            command.add_argument(
                "service",
                metavar=t("服务或任务名"),
                help=t("服务或监控名称（__sync / __watch）；--task 时填写任务名"),
            )
            command.add_argument(
                "--lines",
                type=int,
                default=100,
                choices=range(1, 100001),
                metavar=t("行数"),
                help=t("末尾日志行数，范围 1～100000，默认 100"),
            )
            command.add_argument("--follow", action="store_true", help=t("持续显示新增日志"))
            command.add_argument("--task", action="store_true", help=t("查看准备或构建任务的日志"))
        if action == "rename":
            command.add_argument(
                "new_name",
                metavar=t("新名称"),
                help=t("新的本机注册别名；项目声明中的名称保持原值"),
            )
        if action == "unregister":
            command.add_argument(
                "--purge", action="store_true", help=t("同时删除受控 WSL 工作缓存；Windows 不支持")
            )
