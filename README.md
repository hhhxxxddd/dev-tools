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
- `dev-tools` 自身在 Windows 使用一个未全局激活的私有 Python 3.11；它不参与项目选版。
- Codex、Claude Code、CodeGraph 属于 Windows 操作型 CLI，使用独立配置和宿主 Node；
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

## 更新、卸载与测试

更新仓库后重新运行对应安装脚本即可刷新入口。卸载入口使用
`scripts/uninstall.ps1` 或 `scripts/uninstall.sh`；已安装的项目运行时和缓存不会被删除。

```powershell
$env:PYTHONPATH = "$PWD\src"
$python = Join-Path "$(mise where python@3.11)" python.exe
& $python -m unittest discover -s tests -t . -v
```

## License

Licensed under the [MIT License](LICENSE).
