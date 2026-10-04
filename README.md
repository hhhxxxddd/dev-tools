# dev-tools

**简体中文** · [English](README.en.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

`dev-tools` 是 Windows + WSL 的项目开发工具入口。它从项目已有文件中识别 Java、Node.js、
Python、Maven 和包管理器版本，生成项目级 `mise.toml`，并在明确执行准备命令时安装该项目
缺少的版本。

## 设计边界

- 不为项目创建或安装 Java、Node.js、Python、Maven 等全局默认版本。
- `scan` 和 `init` 只读取已知元数据，不执行项目代码，也不下载运行时。
- 只有显式执行 `project prepare` 才会调用 mise 安装版本。
- Windows 与 WSL 共享项目版本声明，但分别安装平台原生运行时和缓存。
- `dev-tools` 自身固定使用各平台原生 Python 3.14.8，不继承项目的 Python 版本选择。
- Codex、CodeGraph 属于 Windows 操作型 CLI，使用独立配置和宿主 Node；
  `project prepare` 会强制忽略这份配置。

## 命令帮助

先从以下三个入口开始：

```powershell
dev-tools help
dev-tools project --help
dev-tools project prepare --help
```

| 命令 | 是否写文件 | 是否下载 | 用途 |
|---|---:|---:|---|
| `dev-tools --version` | 否 | 否 | 显示当前版本 |
| `dev-tools status` | 否 | 否 | 检查 Windows 与 WSL 的 mise 状态 |
| `dev-tools doctor` | 否 | 否 | 运行两侧 mise 诊断 |
| `dev-tools cli status` | 否 | 否 | 查看 Windows 操作型 CLI |
| `dev-tools cli install` | 否 | 是 | 显式安装缺失的操作型 CLI |
| `dev-tools cli outdated` | 否 | 否 | 检查操作型 CLI 更新 |
| `dev-tools cli upgrade` | 否 | 是 | 显式更新操作型 CLI |
| `dev-tools project scan [PATH]` | 否 | 否 | 报告项目版本声明、来源与冲突 |
| `dev-tools project init [PATH]` | 可能 | 否 | 缺少时生成根级 `mise.toml` |
| `dev-tools project prepare [PATH] --dry-run` | 否 | 否 | 预览该项目需要安装的版本 |
| `dev-tools project prepare [PATH]` | 否 | 是 | 安装该项目 mise 配置声明的缺失版本 |

未提供 `PATH` 时使用当前目录。PowerShell 的 `status` 和 `doctor` 默认同时检查 Windows
与 `Ubuntu` WSL，可用 `-Distro` 指定其他发行版；在 WSL 中执行时只检查当前 WSL。

## 安装

把仓库克隆到 Windows 和 WSL 都能访问的位置：

```powershell
git clone https://github.com/hhhxxxddd/dev-tools.git
cd dev-tools
.\scripts\bootstrap.ps1
```

同时安装配套的
[`wsl-devctl`](https://github.com/hhhxxxddd/wsl-devctl)：

```powershell
.\scripts\bootstrap.ps1 -InstallWslDevctl
```

bootstrap 会：

1. 缺少时通过 Scoop 安装 Windows mise。
2. 缺少时通过 `extrepo + apt` 安装 WSL mise 和 Python 3。
3. 安装 Windows 与 WSL 的 `dev-tools` 命令入口。
4. 安装仅供 `dev-tools` 扫描器使用的 Windows 私有 Python。
5. 可选安装 `wsl-devctl` 并创建 PowerShell 转发命令。

它不会扫描项目、安装项目开发运行时或自动安装 Windows 操作型 CLI。需要这些 CLI 时显式
执行 `dev-tools cli install`。

只安装单侧命令入口：

```powershell
.\scripts\install.ps1
```

```bash
sudo bash scripts/install.sh
```

## 项目工作流

### 1. 查看识别结果

```powershell
dev-tools project scan E:\Projects\MyProjects\some-project
dev-tools project scan E:\Projects\MyProjects\some-project --json
```

### 2. 生成项目声明

```powershell
dev-tools project init E:\Projects\MyProjects\some-project --dry-run
dev-tools project init E:\Projects\MyProjects\some-project
```

已有根级 `mise.toml` 或 `.mise.toml` 时保持原文件不变；同优先级版本冲突时停止并报告。

### 3. 安装项目版本

```powershell
dev-tools project prepare E:\Projects\MyProjects\some-project --dry-run
dev-tools project prepare E:\Projects\MyProjects\some-project
```

`prepare` 要求项目根目录已经存在 `mise.toml` 或 `.mise.toml`。它等价于在目标项目
上下文显式执行 `mise install`，不会安装仓库外的全局默认版本。

## 可识别的声明

- mise：`mise.toml`、`.mise.toml`、`.tool-versions`。
- Java：`.java-version`、`.sdkmanrc`、Maven POM、Gradle toolchain。
- Maven：Maven Wrapper；存在 Wrapper 时不重复生成 Maven 声明。
- Node.js：`.nvmrc`、`.node-version`、`package.json` engines、devEngines、Volta。
- 包管理器：npm、pnpm、Yarn 的 `packageManager`、engines 和 Volta 声明。
- Python：`.python-version`、`pyproject.toml`、`uv.lock` 和 uv 版本要求。

扫描器只解析已知文本、TOML、JSON 和 XML。版本范围会转换成可审阅的宽松版本，例如
`>=3.11` 转为 `python = "3.11"`。

## 与 wsl-devctl 配合

`dev-tools` 负责发现、生成和显式安装项目工具版本；
[`wsl-devctl`](https://github.com/hhhxxxddd/wsl-devctl) 负责把 Windows 源码同步到 WSL
ext4，并管理构建、systemd 进程和热更新。

```bash
dev-tools project init /mnt/e/Projects/CompanyProjects/order-service
wsl-devctl init /mnt/e/Projects/CompanyProjects/order-service \
  --toolchain mise --fix --start
```

也可以由 `wsl-devctl` 调用 `dev-tools` 生成配置：

```bash
wsl-devctl init /mnt/e/Projects/CompanyProjects/order-service \
  --toolchain mise --generate-mise --fix --start
```


Windows 安装器通过个人 mise 的 "conf.d/windows-cli-tools" 目录联接读取仓库中的操作型 CLI 配置，避免复制后版本声明分叉。个人全局运行时配置保留在仓库外；安装器仅清理此前由 dev-tools 管理的全局配置覆盖。CLI 操作与项目准备仍使用各自的显式隔离配置。

## 更新、卸载与测试

更新仓库后重新运行对应安装脚本即可刷新入口。卸载入口使用
`scripts/uninstall.ps1` 或 `scripts/uninstall.sh`；已安装的项目运行时和缓存不会被删除。

```powershell
$env:PYTHONPATH = "$PWD\src"
$python = Join-Path "$(mise where python@3.14.8)" python.exe
& $python -m unittest discover -s tests -t . -v
```

## License

Licensed under the [MIT License](LICENSE).

## 本机速查

`dev-tools sysinfo` 只读查看当前操作系统、CPU 核心、当前 Python 和 PATH 中常见命令的可用性。
它不运行被发现的工具、不联网、不安装、不启动 WSL/服务、不加载 shell profile，也不扫描项目。
Windows 使用 dev-tools 已有的私有 Python；WSL 使用现有 Python 3。

```text
dev-tools sysinfo
dev-tools sysinfo --json
dev-tools sysinfo --config <本机配置路径>
```

可选配置默认为当前检出的 `config/sysinfo.local.json`，已被 Git 忽略；也可用
`DEV_TOOLS_SYSINFO_CONFIG` 指定每台机器自己的文件，`--config` 优先。配置缺失或无效时，
安全回退到通用探测，不创建或覆盖文件；JSON 的 `config_state` 为 missing/invalid/loaded。
新机器无需复制个人机配置。`config/sysinfo.example.json` 提供空的通用结构。

`directories` 条目可含 command、description、windows、wsl；只检查当前侧对应目录是否存在。
`tools` 条目只含 command、description，作为入口速查，shell 函数及服务健康不在探测范围内。
通用命令的 `available_on_path` 仅表示 PATH 能定位，不表示版本、服务状态或存在可用更新。
程序忽略 SSH、密钥、代理、凭据和其他未知字段，普通和 JSON 输出都不会展示这些字段。
不要把秘密放在目录或工具说明中。本机配置保持本地，不提交。

旧 `dev-info` 可通过 `scripts/dev-info.ps1` 或 WSL `scripts/dev-info` 转发到 sysinfo；
兼容入口不读取旧控制仓库的配置。无需重新安装运行时。

## 每日仓库与软件报告

`dev-tools report` 默认刷新 Git 远端引用及软件索引并生成报告；不会执行
Git pull、软件安装/升级，也不会接受新的源协议。`sysinfo` 仍是只读环境速查。

每台机器自行将 `config/report.example.json` 复制为 Git 忽略的
`config/report.local.json`，在 `roots.windows` / `roots.wsl` 填写自己的项目根目录。
不读取 Codex trusted paths 或项目登记，也不依赖 codex-chats。只扫描声明目录，
最大深度 0–5（默认 2），最多 256 个仓库和 10000 个目录；跳过依赖目录、链接和 junction。
发现仓库后不继续扫描其内部。配置不应包含凭据、SSH 或代理信息。

```text
dev-tools report
dev-tools report --json --output report.json
dev-tools report --no-refresh --json
dev-tools report --config PATH --timeout 90
```

`--timeout` 是每条外部命令的超时（秒）。各失败、缺少工具、无上游和无效目录单独报告。
fetch 或 APT/Scoop 刷新失败时标记 cached；未刷新报告不能代表远端最新状态。
Windows 入口采集 winget、Scoop、现有 dev-tools status/cli outdated、WSL APT/mise；
WSL 入口只采集当前 Linux 环境，不自动转发到 Windows。WSL APT 使用非交互 sudo，
权限不足则报告失败。npm 仅查询 mise 之外的额外全局包。
`--no-refresh` 不 fetch 或刷新索引，不查询可能自动刷新源的 winget，但仍查询 CLI 最新版本。
报告含版本与脱敏工具输出；源 URL、主目录和常见敏感值会隐藏。日志不得用于保存凭据。
配置缺失/无效时跳过仓库扫描并报告配置状态，不创建文件。
