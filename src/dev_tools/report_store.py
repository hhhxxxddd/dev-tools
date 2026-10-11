"""Confirm Store bulk candidates without applying updates or prompting the user."""

from __future__ import annotations

import re
import time
from collections import Counter

from .i18n import t
from .presentation import label as display_label
from .report_updates import store_packages, store_update_available


def collect_store_updates(runner, timeout: int) -> dict:
    started = time.monotonic()
    deadline = started + min(timeout, getattr(runner, "timeout", timeout))
    bulk = runner.run(["store", "updates"], input="n\n")
    result = dict(bulk)
    result["coverage_note"] = t(
        "Store CLI 批量结果仅作候选，逐项确认更新；未确认项不列入 updates，目标版本缺失时记为未知"
    )
    if bulk["status"] in {"missing", "timeout", "skipped"}:
        return result
    try:
        candidates = store_packages(bulk.get("output", ""))
    except ValueError, TypeError, KeyError:
        if bulk["status"] == "ok":
            result["status"] = "parse-error"
        return result

    count = re.search(r"Updates available\s*\((\d+) found\)", bulk.get("output", ""))
    incomplete = (
        bulk["status"] != "ok"
        or (count is not None and len(candidates) != int(count[1]))
        or "Failed to read input in non-interactive mode" in bulk.get("output", "")
        or re.search(r"^\s*Error\s*:", bulk.get("output", ""), re.MULTILINE) is not None
    )
    result["discovery"] = {**bulk, "candidates": candidates}
    result["updates"], result["checks"], result["unverified"] = [], [], []
    names = Counter(item["name"].casefold() for item in candidates)
    for item in candidates:
        remaining = deadline - time.monotonic()
        name = item["name"]
        if remaining <= 0:
            check = {"status": "timeout", "output": t("Store 逐项确认已达到采集超时")}
        elif name.startswith("-") or names[name.casefold()] > 1:
            check = {"status": "parse-error", "output": t("Store 候选名称无法安全且唯一地查询")}
        else:
            check = runner.run(["store", "update", name], input="n\n", timeout=remaining)
        check = {**item, **check, "update_available": None}
        if check["status"] not in {"missing", "timeout", "skipped", "parse-error"}:
            error = re.search(r"^\s*Error\s*:", check.get("output", ""), re.MULTILINE)
            try:
                available = store_update_available(check.get("output", ""), name)
            except ValueError:
                if error:
                    check["status"] = "failed"
                elif check["status"] == "ok":
                    check["status"] = "parse-error"
            else:
                if available:
                    check["update_available"] = True
                    result["updates"].append({**item, "available": None})
                    check["status"] = (
                        "updates"
                        if check["status"] == "ok"
                        and not error
                        and "Failed to read input in non-interactive mode"
                        not in check.get("output", "")
                        else "partial"
                    )
                elif check["status"] == "ok" and not error:
                    check["update_available"] = False
                else:
                    check["status"] = "failed"
        result["checks"].append(check)
        if check["update_available"] is None:
            result["unverified"].append({**item, "status": check["status"]})
        if check["status"] not in {"ok", "updates"}:
            incomplete = True
        if check["status"] == "timeout":
            # A timed-out query has consumed the budget; do not start more queries.
            deadline = time.monotonic()

    result["status"] = "partial" if incomplete else "updates" if result["updates"] else "ok"
    result["seconds"] = round(time.monotonic() - started, 2)
    lines = [
        t(
            "Store 候选：{candidates}；已确认更新：{updates}；已确认无更新：{current}；未确认：{unverified}",
            candidates=len(candidates),
            updates=len(result["updates"]),
            current=sum(check["update_available"] is False for check in result["checks"]),
            unverified=len(result["unverified"]),
        )
    ]
    for check in result["checks"]:
        if check["update_available"] is True:
            lines.append(
                t(
                    "{name}：{installed} → {available}",
                    name=check["name"],
                    installed=check["installed"],
                    available=display_label("unknown"),
                )
            )
        elif check["update_available"] is False:
            lines.append(
                t(
                    "{name}：未发现更新（当前 {installed}）",
                    name=check["name"],
                    installed=check["installed"],
                )
            )
        else:
            lines.append(
                t(
                    "{name}：更新状态未确认（{status}）",
                    name=check["name"],
                    status=display_label(check["status"]),
                )
            )
    result["output"] = "\n".join(lines)
    return result
