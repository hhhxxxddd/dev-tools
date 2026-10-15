# 开发与验证

[English](development.en.md) · [返回 README](../README.md)

## 代码结构与验证

```text
src/dev_tools/
  cli.py、command_help.py                 命令定义与本地化帮助
  settings.py、i18n.py、locales/           本机偏好与中英文输出
  scanner.py、metadata.py、versions.py     静态元数据与版本识别
  project_models.py、workflows.py          扫描模型、初始化与 mise 隔离
  projects/                               声明、计划、执行、驱动、生命周期和 worker
  runtimes/router.py                      跨环境命令与路径传输
  runtimes/platforms/                     原生存储、用户、同步、进程与 systemd
scripts/                                  安装与平台入口、底层进程适配
systemd/                                  通用 worker 模板
```

Python 无第三方运行依赖，支持 `>=3.14.8,<3.15`。开发验证使用本平台 Python，并将 src 加入 PYTHONPATH。

控制器可通过本机绑定（Windows `.host-python.win`、WSL `.host-python`）使用外部解释器，更新时沿用该绑定，不管理其升级或卸载。控制器和项目可以共享只读基础 Python，但项目版本声明、虚拟环境和依赖安装仍保持独立；隔离安装测试须覆盖显式路径、重复安装、无 mise 启动、跨平台绑定隔离和无效绑定失败。

Windows：

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m unittest discover -v tests
uvx ruff check src tests
uvx ruff format --check src tests
```

WSL：

```bash
export PYTHONPATH="$PWD/src"
python3 -m unittest discover -v tests
uvx ruff check src tests
uvx ruff format --check src tests
```

入口改动还应验证 `scripts/dev-tools.ps1` 与 `scripts/dev-tools`；bootstrap 改动先解析所有 PowerShell 安装脚本。维护约束见 [AGENTS.md](../AGENTS.md)。

真实验证脚本以临时注册、状态和缓存运行：`tests/integration/lifecycle_smoke.py` 检查自动准备、服务增删、HTTP 服务、热构建、恢复和重命名；`dependency_smoke.py` 使用本地 npm 包验证依赖新增、升级、移除、锁文件回写和停止后补齐，不下载第三方包；`toolchain_smoke.py` 使用已安装的 Java/Node/Python/Maven；`transport_smoke.py` 从 Windows 检查双向路径、隐式名称、帮助、配置和语言传递。WSL 生命周期测试需要 root 和 systemd，工具链测试不安装新版本；双向测试需要两侧入口已安装。

[MIT License](../LICENSE)

`tests/integration/root_windows_transport.py` 在 WSL root 下运行，使用隔离控制器与 Windows 注册、状态、缓存，验证 sudo 清理 Windows PATH 后的版本、SSH、hosts 查询及注册、重命名、注销。源码需位于挂载的 Windows 磁盘；使用已安装的原生 Python，不修改系统 hosts 或业务注册。

`tests/integration/environment_smoke.py` 从 Windows 验证两端默认环境、显式 --all、config/sysinfo/report/ssh/self 的双向操作、目标侧编辑器和配置隔离、中文输出路径及隔离恢复记录；仅使用临时偏好、注册和状态，不更新业务项目或写系统 hosts。`controller_update_smoke.py` 在两端分别验证输入 N 不变、输入 Y 重新部署后恢复原运行子集；Windows 使用隔离 Scoop 目录避免修改真实 profile。

`tests/integration/ssh_smoke.py` 使用隔离的 Win/WSL 控制器、SSH 配置、编辑器和交互客户端，验证真实双向进程链、Include 原文件编辑、标准输入和原生会话退出码；不读取真实 SSH 配置、不使用密钥或连接外部服务器。

## CI 与发行

普通 push/PR 在 Windows 和 Ubuntu 上运行 Python 3.14.8 单元/契约测试、Ruff、PowerShell 解析、Bash/ShellCheck、文档与 TOML 校验和解压包入口验证。运行 `python -m dev_tools.validation` 检查版本、本地链接、双语章节结构与 TOML。验证工具固定在 `requirements-dev.txt`，GitHub Actions 固定经核对的提交 SHA。

`vX.Y.Z` tag 复用这些检查，并从干净且匹配 tag 的提交构建正式产物。Windows/Linux 产物字节必须一致才能发布。重试时核对已发布附件，不覆盖正式附件。每次正式 Release 生成 `dev-tools.json` 附件，供 hxd-box 同步维护 bucket 清单；本仓库工作流不自动向 bucket 推送更新。

Ubuntu 适配器测试不能代替真实 WSL 验证。发布前运行已有隔离 WSL 生命周期、依赖、工具链脚本及 Windows→WSL 部署与传输脚本，要求 systemd 和已安装的原生运行时。控制器包检查在临时原生目录重复部署两次，确认解释器绑定、注册和状态保留。不要用活动注册或中间件数据充当测试数据。

公开 CLI 约定见[接口文档](cli-contract.md)。候选验证：`python -m dev_tools.release --output dist`，随后 `python -m dev_tools.package_smoke dist/dev-tools-0.5.2.zip`。候选可标记源码未提交；`--tag v0.5.2 --official` 要求源码已提交、工作区干净且 tag 对应当前提交。
