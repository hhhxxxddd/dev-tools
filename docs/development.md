# 开发与验证

[English](development.en.md) · [返回 README](../README.md)

## 代码结构与验证

```text
src/dev_tools/
  cli.py、command_help.py                 命令定义与本地化帮助
  settings.py、i18n.py、locales/           本机偏好与中英文输出
  scanner.py、metadata.py、versions.py     静态元数据与版本识别
  inspection.py、constraints.py           项目范围、单次元数据快照与保守约束校验
  frameworks.py                           静态框架关联目录规则
  project_models.py、workflows.py          扫描模型、初始化与 mise 隔离
  ssh.py、ssh_config.py、hosts*.py         SSH 与 Windows hosts 工具
  toolbox/                                端口、PATH、网络诊断与原生采集
  projects/                               声明、计划、执行、驱动、生命周期和 worker
    lifecycle.py                          服务选择、启动结果、回滚范围与恢复策略
  runtimes/router.py                      跨环境命令与路径传输
  runtimes/platforms/                     原生存储、用户、同步、进程与 systemd
scripts/                                  安装与平台入口、底层进程适配
systemd/                                  通用 worker 模板
```

Python 无第三方运行依赖，支持 `>=3.14.8,<3.15`。开发验证使用本平台 Python，并将 src 加入 PYTHONPATH。

框架模块只根据元数据名称提供关联目录和标记文件，不读取文件或执行项目代码；inspection 统一执行路径安全、排除规则、范围展开和快照缓存。新增框架约定集中在 frameworks.py，公共扫描层不包含框架目录名称。

项目引擎编排准备、同步、监控与状态持久化；lifecycle.py 统一服务依赖选择、启动结果和恢复策略，经平台适配器执行进程操作。必需服务的依赖始终按必需服务等待就绪；可选服务失败单独记录，显式启动报告失败，后台恢复保留共享服务。致命失败只回滚本次新启动的 worker，并保留原始错误及清理错误；维护按活动服务和启动意图恢复，显式 stop 取消恢复。不在这些模块中加入应用数据、令牌、会话或 sidecar 管理。

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

生命周期测试先等待空闲退避，再验证改动唤醒与应用 PID；WSL 还确认同步没有停止或一直维持快速轮询。仓库位于 Windows 挂载盘时，可让测试源码放在该盘，注册、状态和缓存仍使用 Linux 临时目录：

```bash
mkdir -p dist/integration-sources
export DEV_TOOLS_TEST_SOURCE_ROOT="$PWD/dist/integration-sources"
python3 tests/integration/lifecycle_smoke.py
python3 tests/integration/dependency_smoke.py
unset DEV_TOOLS_TEST_SOURCE_ROOT
```

监控回归覆盖缓存过期与启动隔离、构建前 Git 状态复核、停止取消维护和快照交接；访问回归覆盖接受连接但不响应 HTTP、IPv4/IPv6、普通用户读取 root 托管项目，以及符号链接/FIFO 元数据跳过。

[MIT License](../LICENSE)

`tests/integration/root_windows_transport.py` 在 WSL root 下运行，使用隔离控制器与 Windows 注册、状态、缓存，验证 sudo 清理 Windows PATH 后的版本、SSH、hosts 查询及注册、重命名、注销。源码需位于挂载的 Windows 磁盘；使用已安装的原生 Python，不修改系统 hosts 或业务注册。

`tests/integration/environment_smoke.py` 从 Windows 验证两端默认环境、显式 --all、config/sysinfo/report/ssh/self 的双向操作、目标侧编辑器和配置隔离、中文输出路径及隔离恢复记录；仅使用临时偏好、注册和状态，不更新业务项目或写系统 hosts。`controller_update_smoke.py` 在两端分别验证输入 N 不变、输入 Y 重新部署后恢复原运行子集；Windows 使用隔离 Scoop 目录避免修改真实 profile。

`tests/integration/ssh_smoke.py` 使用隔离的 Win/WSL 控制器、SSH 配置、编辑器和交互客户端，验证真实双向进程链、Include 原文件编辑、标准输入和原生会话退出码；不读取真实 SSH 配置、不使用密钥或连接外部服务器。

该脚本还验证 SSH 列表的原生/双向保存与取消，以及 Windows hosts 列表的原生/WSL 调用，所有写入仅发生在临时文件。`tests/integration/list_terminal_smoke.py` 在原生 WSL 的真实 PTY 中验证字段编辑、保存、Esc 撤销、Ctrl+C 退出和终端模式恢复，使用隔离用户目录。列表导航、输入校验、来源追踪、并发写入、原始字节备份与多文件失败回滚另有单元测试覆盖。

`tests/integration/project_logs_smoke.py` 使用隔离控制器、注册和日志，验证 Win/WSL 原生与双向项目汇总、原始单服务日志、任务日志、等待新文件和持续输出，以及中断返回 130；不停止业务项目、不修改已部署控制器。

## 性能与边界

Ruff 同时检查导入排序、易错写法和 Python 现代语法。扫描与监控跳过符号链接、Windows 目录联接和非普通元数据文件；测试覆盖畸形声明、损坏注册隔离、Git 未提交状态、分离 HEAD、packed refs 和 worktree。

Windows 端口采集直接调用 IP Helper 和 Toolhelp API，无第三方运行依赖；先过滤端口和协议，再读取关联进程及祖先进程。WSL 仅为匹配 socket 读取进程详情，无匹配时跳过 PID 与注册遍历。自动发现只遍历一次元数据目录；Git 监控每轮调用一次 Git，仍保留内容指纹和操作中标记检查。

2026-10-16 本机 Windows、Python 3.14.8，以同一 PowerShell 入口分别运行 3 次、取端到端耗时中位数（含启动开销）：

| 查询 | 优化前 | 优化后 |
| --- | --- | --- |
| 无匹配端口 | 8.58 秒 | 0.63 秒 |
| 全部监听与绑定端口 | 8.16 秒 | 1.82 秒 |

结果受进程数量、缓存和系统负载影响，不作为固定耗时承诺。真实 socket 的 IPv4/IPv6、TCP/UDP 和进程身份由原生测试验证；不以减少归属校验或持久化快照换取速度。

## CI 与发行

`tests/integration/toolbox_smoke.py` 使用隔离的 Windows/WSL 控制器和临时 HTTP 服务，验证 ports/which/net 的两端原生执行及双向调用、实际 socket PID、HEAD 响应与查询参数隐藏；不修改业务注册或系统网络配置。

普通 push/PR 在 Windows 和 Ubuntu 上运行 Python 3.14.8 单元/契约测试、Ruff、PowerShell 解析、Bash/ShellCheck、文档与 TOML 校验和解压包入口验证。运行 `python -m dev_tools.validation` 检查版本、本地链接、双语章节结构与 TOML。验证工具固定在 `requirements-dev.txt`，GitHub Actions 固定经核对的提交 SHA。

`vX.Y.Z` tag 复用这些检查，并从干净且匹配 tag 的提交构建正式产物。Windows/Linux 产物字节必须一致才能发布。重试时核对已发布附件，不覆盖正式附件。每次正式 Release 生成 `dev-tools.json` 附件，供 hxd-box 同步维护 bucket 清单；本仓库工作流不自动向 bucket 推送更新。

Ubuntu 适配器测试不能代替真实 WSL 验证。发布前运行已有隔离 WSL 生命周期、依赖、工具链脚本及 Windows→WSL 部署与传输脚本，要求 systemd 和已安装的原生运行时。控制器包检查在临时原生目录重复部署两次，确认解释器绑定、注册和状态保留。不要用活动注册或中间件数据充当测试数据。

公开 CLI 约定见[接口文档](cli-contract.md)。候选验证：`python -m dev_tools.release --output dist`，随后 `python -m dev_tools.package_smoke dist/dev-tools-0.5.7.zip`。候选可标记源码未提交；`--tag v0.5.7 --official` 要求源码已提交、工作区干净且 tag 对应当前提交。
