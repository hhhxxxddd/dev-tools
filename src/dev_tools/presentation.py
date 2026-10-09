"""Localized human labels; structured identifiers remain stable."""

from __future__ import annotations

import unicodedata

from .i18n import t

LABELS = {
    "windows": "Windows",
    "wsl": "WSL",
    "all": "全部环境",
    "register": "注册项目",
    "list": "项目列表",
    "prepare": "准备项目",
    "start": "启动项目",
    "stop": "停止项目",
    "restart": "重启项目",
    "status": "项目状态",
    "show": "项目配置与绑定",
    "build": "构建项目",
    "rename": "重命名项目",
    "unregister": "注销项目",
    "created": "已生成",
    "preserved": "保留已有文件",
    "preview": "待生成（仅预览）",
    "no-tools": "未识别到工具版本",
    "unresolved": "仍有待解决问题",
    "exact": "明确声明",
    "compatible": "兼容版本要求",
    "starting": "启动中",
    "running": "运行中",
    "restarting": "重启中",
    "not-ready": "未就绪",
    "partially-running": "部分运行",
    "recovery-pending": "待恢复",
    "stopped": "已停止",
    "healthy": "正常",
    "unhealthy": "异常",
    "unknown": "未知",
    "loaded": "已加载",
    "missing": "未配置",
    "invalid": "无效",
    "ok": "成功",
    "failed": "失败",
    "partial": "部分完成",
    "updates": "存在可更新项",
    "parse-error": "解析失败",
    "timeout": "超时",
    "skipped": "已跳过",
    "refreshed": "已刷新",
    "cached": "缓存结果",
    "clean": "无修改",
    "dirty": "有未提交修改",
    "error": "检查失败",
    "no-upstream": "未设置上游分支",
    "tracking-error": "无法检查分支关系",
    "diverged": "与上游分支存在分歧",
    "ahead": "领先上游分支",
    "behind": "落后上游分支",
    "even": "与上游分支一致",
    "invalid-path": "目录不存在",
    "scan-limit": "已达到扫描上限",
    "unreadable": "目录无法读取",
}


def label(value: str | bool | None) -> str:
    if value is None:
        return t("未知")
    if value is True:
        return t("是")
    if value is False:
        return t("否")
    return t(LABELS.get(value, value))


def format_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    def width(value: str) -> int:
        return sum(
            0
            if unicodedata.combining(char)
            else 2
            if unicodedata.east_asian_width(char) in "WF"
            else 1
            for char in value
        )

    widths = [max(width(row[index]) for row in [headers, *rows]) for index in range(len(headers))]

    def line(row):
        return "  ".join(
            value + " " * (size - width(value)) for value, size in zip(row, widths, strict=True)
        ).rstrip()

    return "\n".join([line(headers), line(tuple("-" * size for size in widths)), *map(line, rows)])
