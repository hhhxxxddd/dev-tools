from __future__ import annotations

import argparse
import json

from ..i18n import error_detail, message, render, t
from ..presentation import label
from ..runtimes.platforms.factory import backend_for
from ..runtimes.platforms.layout import require_registration_control
from ..runtimes.platforms.locking import operation_lock
from . import SCHEMA_VERSION
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
                value=label(
                    "unknown"
                    if payload["healthy"] is None
                    else "healthy"
                    if payload["healthy"]
                    else "unhealthy"
                ),
            )
        )
        for name, value in payload["services"].items():
            print(f"  {name}：{label(value['phase'])}（{label(value['health'])}）")
        if payload.get("recovery"):
            recovery = payload["recovery"]
            print(t("  待恢复操作：{value}", value=label(recovery.get("operation", "unknown"))))
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
        for project in payload["projects"]:
            print(
                t(
                    "  {name}：服务就绪 {ready} {value}",
                    name=project["name"],
                    ready=label(project["ready"]),
                    value=render(project.get("error_detail") or project.get("error", "")),
                ).rstrip()
            )
        if not payload["projects"]:
            print(t("  暂无已注册项目。"))
    if "runtime_versions" in payload:
        print(t("  运行时版本：") + (", ".join(payload["runtime_versions"]) or t("无")))
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
    registry = Registry(environment)
    payload = {"schema_version": SCHEMA_VERSION, "action": action, "environment": environment}
    if action == "register":
        from pathlib import Path

        require_registration_control(environment)
        binding = registry.register(
            Path(args.path), name=args.name, run_user=args.user, force=args.force
        )
        emit({**payload, "binding": binding.as_dict()}, args.json)
        return 0
    if action == "list":
        projects = []
        for name in registry.names():
            try:
                projects.append(engine_for(environment, name).status())
            except (ProjectError, OSError) as exc:
                projects.append(
                    {
                        "name": name,
                        "ready": False,
                        "error": str(exc),
                        "error_detail": error_detail(exc),
                    }
                )
        emit({**payload, "projects": projects}, args.json)
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
    if action == "prepare":
        if args.json and not args.dry_run:
            raise ProjectError(message("prepare 使用 --json 时必须同时指定 --dry-run"))
        plan = engine.plan()
        if args.dry_run:
            emit(plan.as_dict(), args.json)
            return 2 if plan.unresolved else 0
        engine.prepare(plan)
    elif action == "start":
        engine.start((args.service,) if args.service else None)
    elif action == "stop":
        engine.stop()
    elif action == "restart":
        engine.restart()
    elif action == "status":
        emit(engine.status(), args.json)
        return 0
    elif action == "show":
        emit(
            {**payload, "binding": engine.binding.as_dict(), "project": engine.spec.raw}, args.json
        )
        return 0
    elif action == "sync":
        engine.backend.require_control()
        with operation_lock(engine.binding.state, timeout=15):
            engine.backend.sync(engine.spec)
    elif action == "build":
        engine.rebuild(service=args.service, kind=args.kind)
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
    listing = commands.add_parser("list", help=t("列出所选环境的注册项目和状态"))
    listing.add_argument("--json", action="store_true", help=t("以 JSON 输出结果"))
    listing.set_defaults(func=run)
    for action, description in (
        ("prepare", t("准备项目运行时、依赖和构建；--dry-run 预览计划")),
        ("start", t("启动已准备的项目服务和监控")),
        ("stop", t("停止项目服务和监控")),
        ("restart", t("重启项目服务和监控")),
        ("status", t("查看项目准备情况、服务状态和健康检查")),
        ("build", t("使用已准备的运行时重新构建项目")),
        ("logs", t("查看指定服务的日志")),
        ("unregister", t("停止服务并注销；默认保留源码、缓存和运行时")),
        ("show", t("查看项目声明和本机绑定")),
        ("sync", t("将源码同步到原生工作目录")),
        ("rename", t("修改注册名称并恢复原先运行的服务")),
    ):
        command = commands.add_parser(
            action,
            help=description,
            group=t("进阶命令") if action in {"show", "sync", "rename"} else t("常用命令"),
        )
        command.add_argument(
            "name", nargs="?", metavar=t("项目名"), help=t("注册名称；省略时按当前目录唯一匹配")
        )
        command.set_defaults(func=run)
        if action != "logs":
            command.add_argument(
                "--json",
                action="store_true",
                help=t("以 JSON 输出准备计划，须同时指定 --dry-run")
                if action == "prepare"
                else t("以 JSON 输出结果"),
            )
        if action == "prepare":
            command.add_argument(
                "--dry-run",
                action="store_true",
                help=t("只校验并预览准备计划，不安装或执行项目任务"),
            )
        if action in {"start", "build"}:
            command.add_argument(
                "--service",
                metavar=t("服务名"),
                help=t("仅操作指定服务；启动时包含其依赖，省略时操作全部服务"),
            )
        if action == "build":
            command.add_argument(
                "--kind",
                choices=("branch", "source", "resource", "structural"),
                default="branch",
                metavar=t("构建类型"),
                help=t(
                    "branch 分支变化（默认）；source 源码变化；resource 资源变化；structural 结构变化"
                ),
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
