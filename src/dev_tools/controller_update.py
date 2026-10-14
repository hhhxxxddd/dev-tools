"""Native controller update transactions and recovery through a fresh entrypoint."""

from __future__ import annotations

import argparse
import fnmatch
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .i18n import language, language_scope, message, render, t
from .presentation import error, warning
from .projects.cli import engine_for
from .projects.drivers import spring_classpath
from .projects.models import ProjectError, dependency_order, validate_name
from .projects.registry import Registry, read_json, write_json
from .runtimes.platforms.layout import storage
from .runtimes.platforms.locking import operation_lock

OWNED = (
    "src",
    "scripts",
    "config",
    "docs",
    "examples",
    "systemd",
    ".host-python",
    ".windows-source",
    ".source",
    ".release.json",
    "README.md",
    "README.en.md",
    "CHANGELOG.md",
    "AGENTS.md",
    "LICENSE",
    "pyproject.toml",
)


def native_environment():
    return "windows" if os.name == "nt" else "wsl"


def recovery_path():
    return storage(native_environment())[1] / ".controller-update" / "recovery.json"


def read_recovery(path):
    path = Path(path)
    if path.is_symlink() or path.resolve() != recovery_path().resolve():
        raise ValueError(message("Invalid controller recovery record"))
    record = read_json(path)
    if (
        type(record.get("schema_version")) is not int
        or record["schema_version"] != 1
        or record.get("environment") != native_environment()
        or not isinstance(record.get("projects"), list)
        or not isinstance(record.get("phase"), str)
        or record["phase"] not in {"stopping", "stopped", "restored", "recovery_pending"}
        or type(record.get("controller_ready", True)) is not bool
    ):
        raise ValueError(message("Invalid controller recovery record"))
    names = set()
    for item in record["projects"]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ProjectError(message("Invalid controller recovery record"))
        try:
            validate_name(item["name"])
            workers = item.get("workers")
            intent = item.get("intent", {})
            if (
                item["name"] in names
                or not isinstance(workers, list)
                or any(not isinstance(worker, str) for worker in workers)
                or len(set(workers)) != len(workers)
                or not isinstance(intent, dict)
                or any(type(item.get(flag, False)) is not bool for flag in ("paused", "restored"))
                or item.get("stop_token") is not None
                and not isinstance(item["stop_token"], str)
            ):
                raise ValueError
            selected = intent.get("selected")
            if (
                selected is not None
                and (
                    not isinstance(selected, list)
                    or any(not isinstance(name, str) for name in selected)
                )
                or "running" in intent
                and type(intent["running"]) is not bool
                or intent.get("token") is not None
                and not isinstance(intent["token"], str)
            ):
                raise ValueError
            for name in selected or []:
                validate_name(name)
            for worker in workers:
                if worker not in {"__watch", "__sync"}:
                    validate_name(worker)
        except ValueError:
            raise ValueError(message("Invalid controller recovery record")) from None
        names.add(item["name"])
    return record


def active_projects():
    records = []
    for name in Registry(native_environment()).names():
        engine = engine_for(native_environment(), name, allow_stale=True)
        workers = engine._active()
        if workers:
            records.append({"name": name, "workers": list(workers), "intent": engine.intent()})
    return records


def verify_deployed_files(source, root):
    for folder in ("src/dev_tools", "scripts", "config", "docs", "examples", "systemd"):
        for original in (source / folder).rglob("*"):
            if (
                not original.is_file()
                or "__pycache__" in original.parts
                or original.suffix == ".pyc"
                or folder == "config"
                and fnmatch.fnmatch(original.name, "*.local.*")
            ):
                continue
            deployed = root / original.relative_to(source)
            if not deployed.is_file() or deployed.read_bytes() != original.read_bytes():
                raise ValueError(message("Controller deployment verification failed"))


def recover_controller():
    """A repaired deployment can resume a recovery previously blocked by incomplete files."""
    path = recovery_path()
    record = read_recovery(path)
    if record.get("controller_ready") is False:
        from .controller import REPOSITORY, verify_source

        root = Path(
            os.environ.get(
                "DEV_TOOLS_INSTALL_ROOT", str(REPOSITORY) if os.name == "nt" else "/opt/dev-tools"
            )
        )
        if (root / ".release.json").is_file():
            verify_source(root)
        elif (root / ".source").is_file():
            source = Path((root / ".source").read_text(encoding="utf-8").strip())
            verify_source(source)
            verify_deployed_files(source, root)
        else:
            raise ValueError(
                message(
                    "Controller files are incomplete; repair deployment before recovering projects"
                )
            )
        record["controller_ready"] = True
        write_json(path, record)
    return restore_receipt(path)


def restore_receipt(path):
    path = Path(path)
    record = read_recovery(path)
    if record.get("controller_ready") is False:
        raise ValueError(
            message("Controller files are incomplete; repair deployment before recovering projects")
        )
    failures = []
    for item in record["projects"]:
        if not item.get("paused") or item.get("restored"):
            continue
        name = item["name"]
        try:
            validate_name(name)
            engine = engine_for(native_environment(), name)
            engine.backend.require_control()
            with operation_lock(engine.binding.state, timeout=15):
                if engine.intent().get("token") != item.get("stop_token"):
                    item["restored"] = True
                    item["cancelled"] = True
                    print(
                        warning(
                            t(
                                "Project intent changed during update; skipped restoration: {project}",
                                project=name,
                            )
                        )
                    )
                    continue
                workers = tuple(item["workers"])
                services = tuple(worker for worker in workers if not worker.startswith("__"))
                monitors = tuple(worker for worker in workers if worker.startswith("__"))
                plan = engine.plan()
                if any(worker not in plan.spec.services for worker in services) or any(
                    worker not in {"__sync", "__watch"} for worker in monitors
                ):
                    raise ValueError(message("Previously active services are no longer declared"))
                graph = {name: service.depends_on for name, service in plan.spec.services.items()}
                if set(dependency_order(graph, services)) - set(services):
                    raise ValueError(
                        message(
                            "Previously active services require additional services; review the project before recovery"
                        )
                    )
                engine._validate_plan(plan)
                previous = item.get("intent", {})
                intent = engine.set_intent(
                    running=True, selected=previous.get("selected", list(services))
                )
                # Remember only our token, so an external stop still cancels retries.
                item["stop_token"] = intent["token"]
                write_json(path, record)
                engine._prepare_for_start(plan, intent["token"])
                engine.backend.sync(engine.spec)
                for service in services:
                    spring_classpath(engine.binding, engine.backend, engine.spec.services[service])
                engine._start(
                    services,
                    infrastructure=bool(monitors),
                    monitors=monitors,
                    intent_token=intent["token"],
                )
                item["restored"] = True
                item.pop("error", None)
                print(t("Restored {project}: {workers}", project=name, workers=", ".join(workers)))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            item["error"] = render(exc)
            failures.append(name)
            print(
                error(
                    t("Could not restore {project}: {detail}", project=name, detail=render(exc)),
                    stream=sys.stderr,
                ),
                file=sys.stderr,
            )
        finally:
            write_json(path, record)
    record["phase"] = "recovery_pending" if failures else "restored"
    write_json(path, record)
    if failures:
        print(
            warning(
                t("Recovery record: {path}. Retry with: dev-tools self recover", path=path),
                stream=sys.stderr,
            ),
            file=sys.stderr,
        )
        return 1
    return 0


class UpdateSession:
    def __init__(self, *, yes=False, entry=None, scoop=False, fallback_source=None):
        self.yes = yes
        self.entry = entry
        self.scoop = scoop
        self.fallback_source = fallback_source
        self.path = recovery_path()
        self.record = None
        self.controller_ready = True

    def stop(self):
        existing = read_json(self.path)
        if existing.get("phase") not in {None, "restored"}:
            raise ValueError(
                message("A controller update needs recovery; run dev-tools self recover first")
            )
        projects = active_projects()
        if projects:
            print(
                warning(
                    t(
                        "Running projects affected by this update: {projects}",
                        projects=", ".join(item["name"] for item in projects),
                    )
                )
            )
        if projects and not self.yes:
            try:
                answer = input(warning(t("Stop these projects, update, then restore them? [y/N] ")))
            except EOFError:
                answer = ""
            if answer.strip().lower() not in {"y", "yes"}:
                raise ValueError(
                    message(
                        "Controller update cancelled; no projects were stopped. Use --yes for unattended updates"
                    )
                )
        self.record = {
            "schema_version": 1,
            "environment": native_environment(),
            "phase": "stopping",
            "projects": projects,
        }
        write_json(self.path, self.record)
        for item in projects:
            engine = engine_for(native_environment(), item["name"], allow_stale=True)
            item["paused"] = True
            write_json(self.path, self.record)
            try:
                intent = engine.set_intent(running=False)
                item["stop_token"] = intent["token"]
                write_json(self.path, self.record)
                # Cancel maintenance before waiting for the lock held by a monitor.
                for worker in ("__watch", "__sync"):
                    if engine.backend.active(worker):
                        engine.backend.stop(worker)
                with operation_lock(engine.binding.state, timeout=15):
                    engine._stop(tuple(item["workers"]))
            finally:
                write_json(self.path, self.record)
        self.record["phase"] = "stopped"
        write_json(self.path, self.record)
        if active_projects():
            raise ValueError(
                message("New active projects appeared during confirmation; retry the update")
            )

    def restore(self):
        if self.record is None:
            return 0
        self.record["controller_ready"] = self.controller_ready
        write_json(self.path, self.record)
        if not self.record["projects"]:
            self.record["phase"] = "restored"
            write_json(self.path, self.record)
            return 0
        if not self.controller_ready:
            print(
                error(
                    t(
                        "Controller files are incomplete; repair deployment before recovering projects"
                    ),
                    stream=sys.stderr,
                ),
                file=sys.stderr,
            )
            print(
                warning(
                    t(
                        "Recovery record: {path}. Retry with: dev-tools self recover",
                        path=self.path,
                    ),
                    stream=sys.stderr,
                ),
                file=sys.stderr,
            )
            return 1
        environment = os.environ.copy()
        environment["DEV_TOOLS_TRANSPORT_LANGUAGE"] = language()
        if os.name == "nt":
            if self.scoop:
                # Resolve after Scoop changes its current junction and shim.
                command = [
                    "pwsh",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "$entry = Get-Command dev-tools -CommandType Application -ErrorAction Stop; & $entry.Source _controller-restore; exit $LASTEXITCODE",
                ]
            else:
                command = [
                    "pwsh",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(self.entry),
                    "_controller-restore",
                ]
        else:
            command = ["bash", str(self.entry), "_controller-restore"]
            if (
                self.fallback_source
                and not (self.entry.parent.parent / "src/dev_tools/controller_update.py").is_file()
            ):
                command = [sys.executable, "-P", "-m", "dev_tools.cli", "_controller-restore"]
                environment["PYTHONPATH"] = str(self.fallback_source / "src")
        try:
            code = subprocess.run(command, check=False, env=environment).returncode
        except (OSError, subprocess.SubprocessError) as exc:
            print(error(render(exc), stream=sys.stderr), file=sys.stderr)
            code = 1
        if code:
            print(
                warning(
                    t(
                        "Recovery record: {path}. Retry with: dev-tools self recover",
                        path=self.path,
                    ),
                    stream=sys.stderr,
                ),
                file=sys.stderr,
            )
        return code


def update_with_recovery(session, action, *, stop=True):
    """A stop failure and an interrupted/failed update still get a recovery attempt."""
    with operation_lock(session.path.parent, timeout=15):
        try:
            if stop:
                session.stop()
            code = action()
        except BaseException:
            session.restore()
            raise
        return session.restore() or code


def deploy_wsl(source, *, yes=False):
    from .controller import verify_source

    if os.name == "nt" or os.geteuid() != 0:
        raise ValueError(message("controller deployment requires root; rerun with sudo"))
    source = Path(source).resolve()
    verify_source(source)
    root = Path(os.environ.get("DEV_TOOLS_INSTALL_ROOT", "/opt/dev-tools")).resolve()
    if (
        root == Path("/")
        or root == source
        or source.is_relative_to(root)
        or root.is_relative_to(source)
    ):
        raise ValueError(message("Installation root must be separate from the source"))
    session = UpdateSession(yes=yes, entry=root / "scripts/dev-tools", fallback_source=source)
    units = Path(os.environ.get("DEV_TOOLS_UNIT_DIR", "/etc/systemd/system"))

    def install():
        # Only controller-owned files are backed up. Host runtimes and project data stay native.
        with tempfile.TemporaryDirectory(prefix="dev-tools-controller-backup-") as temporary:
            backup = Path(temporary)
            present = []
            for name in OWNED:
                target = root / name
                if target.is_symlink():
                    raise ValueError(message("Invalid controller installation target"))
                if target.is_dir():
                    shutil.copytree(target, backup / name, symlinks=True)
                    present.append(name)
                elif target.is_file():
                    shutil.copy2(target, backup / name)
                    present.append(name)
            saved_units = []
            (backup / "installed-units").mkdir()
            for unit in units.glob("dev-tools-*.service"):
                if unit.is_symlink():
                    raise ValueError(message("Invalid controller installation target"))
                shutil.copy2(unit, backup / "installed-units" / unit.name)
                saved_units.append(unit.name)
            try:
                session.controller_ready = False
                code = subprocess.run(
                    ["bash", str(source / "scripts/install.sh")], check=False
                ).returncode
                if code == 0:
                    verify_deployed_files(source, root)
                    session.controller_ready = True
            except BaseException:
                rollback(backup, present, saved_units)
                raise
            if code:
                rollback(backup, present, saved_units)
            return code

    def rollback(backup, present, saved_units):
        # Remove markers and owned directories introduced by a failed deployment.
        for name in set(OWNED) - set(present):
            target = root / name
            if target.is_symlink() or not target.resolve().is_relative_to(root):
                raise ValueError(message("Invalid controller installation target"))
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
        for name in present:
            saved, target = backup / name, root / name
            if target.is_symlink():
                raise ValueError(message("Invalid controller installation target"))
            if saved.is_dir():
                subprocess.run(
                    [
                        "rsync",
                        "-a",
                        "--checksum",
                        "--delete",
                        "--delete-excluded",
                        "--exclude",
                        "__pycache__",
                        str(saved) + "/",
                        str(target) + "/",
                    ],
                    check=True,
                )
            else:
                shutil.copy2(saved, target)
        for unit in units.glob("dev-tools-*.service"):
            if unit.name not in saved_units:
                if unit.is_symlink():
                    raise ValueError(message("Invalid controller installation target"))
                unit.unlink()
        for unit in (backup / "installed-units").glob("dev-tools-*.service"):
            if (units / unit.name).is_symlink():
                raise ValueError(message("Invalid controller installation target"))
            shutil.copy2(unit, units / unit.name)
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        session.controller_ready = True
        print(
            warning(
                t("Controller update failed; previous controller files were restored"),
                stream=sys.stderr,
            ),
            file=sys.stderr,
        )

    return update_with_recovery(session, install)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    from .settings import load_settings

    with language_scope(
        os.environ.get("DEV_TOOLS_TRANSPORT_LANGUAGE")
        or load_settings(tolerate_invalid=True).language
    ):
        try:
            raise SystemExit(deploy_wsl(args.source, yes=args.yes))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print(error(render(exc), stream=sys.stderr), file=sys.stderr)
            raise SystemExit(1) from None


if __name__ == "__main__":
    main()
