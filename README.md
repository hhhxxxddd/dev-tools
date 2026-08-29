# dev-tools

[中文](README.md) | [English](README.en.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

`dev-tools` 是个人 Windows + WSL 开发环境的统一命令入口。它组织 mise 的共享运行时管理，
并把项目已有的版本声明归一化为项目级 `mise.toml`。

它不替代 Scoop、winget、APT、Maven、pnpm、uv 或 `wsl-devctl`，也不会执行扫描项目中的
脚本。

## 配套项目

[`wsl-devctl`](https://github.com/hhhxxxddd/wsl-devctl) 负责把 Windows 源码安全同步到
WSL ext4，并管理项目构建、systemd 进程和热更新；`dev-tools` 为它提供共享运行时声明与
项目级 `mise.toml`。

## 安装

克隆到 Windows 与 WSL 都能访问的位置：

```powershell
git clone https://github.com/hhhxxxddd/dev-tools.git
cd dev-tools
```

推荐从 PowerShell 一次完成 Windows、Ubuntu WSL 和共享运行时初始化：

```powershell
.\scripts\bootstrap.ps1 -InstallWslDevctl
```

该命令会：

1. 缺少时通过 Scoop 安装 Windows mise。
2. 缺少时通过 mise 官方文档推荐的
   [`extrepo + apt`](https://mise.jdx.dev/installing-mise.html#apt) 方式安装 WSL mise。
3. 安装 Windows 和 WSL 两侧的 `dev-tools` 入口。
4. 安装共享 `config/mise.toml` 声明的两侧原生运行时。
5. 可选安装同级目录中的 `wsl-devctl`，并在 PowerShell 中增加透明转发命令。

常用选项：

```powershell
.\scripts\bootstrap.ps1 -Distro Ubuntu -InstallWslDevctl
.\scripts\bootstrap.ps1 -InstallWslDevctl -WslDevctlPath E:\Projects\MyProjects\wsl-devctl
.\scripts\bootstrap.ps1 -SkipRuntimeInstall
```

Windows 和 WSL 的二进制、缓存与 PATH 仍保持平台原生；bootstrap 统一的是安装流程、版本
声明和命令入口。

只安装单侧入口时可以分别执行：

```powershell
.\scripts\install.ps1
```

```bash
sudo bash scripts/install.sh
```

脚本根据仓库实际位置配置入口，不绑定用户名或盘符。卸载入口使用对应的
`uninstall.ps1` 或 `uninstall.sh`；已安装的运行时和缓存不会被删除。

安装 `wsl-devctl` 后，PowerShell 可以直接执行：

```powershell
wsl-devctl help
wsl-devctl status local-my-app
```

命令会透明转发到配置的 WSL 发行版。

## 环境管理

在 PowerShell 中同时管理 Windows 和 Ubuntu WSL：

```powershell
dev-tools status
dev-tools install
dev-tools outdated
dev-tools upgrade
dev-tools prune
dev-tools doctor
```

在 WSL 中运行相同命令时，只管理当前 WSL。

共享默认版本位于 [`config/mise.toml`](config/mise.toml)。Windows 与 WSL 使用相同声明、
各自安装平台原生运行时，不共享可执行目录和缓存。

## 项目版本扫描

只扫描并报告：

```bash
dev-tools project scan .
dev-tools project scan . --json
```

预览或生成项目配置：

```bash
dev-tools project init . --dry-run
dev-tools project init .
```

扫描器目前识别：

- `mise.toml`、`.mise.toml`、`.tool-versions`。
- Java 的 `.java-version`、`.sdkmanrc`、Maven POM、Gradle toolchain。
- Maven Wrapper；存在 Wrapper 时不重复生成 mise Maven 声明。
- Node 的 `.nvmrc`、`.node-version`、`package.json` engines、devEngines、Volta。
- npm、pnpm、Yarn 的 `packageManager`、engines 和 Volta 声明。
- Python 的 `.python-version`、`pyproject.toml`、`uv.lock`，以及 uv 自身版本要求。

安全规则：

- 只解析已知文本、TOML、JSON 和 XML，不执行项目代码。
- 已有根级 `mise.toml` 或 `.mise.toml` 时不改写。
- 同优先级声明冲突时退出并报告，不生成文件。
- 版本范围转换为可审计的宽松版本，例如 `>=3.11` 转为 `python = "3.11"`。
- 扫描结果包含来源；生成内容应由用户审阅并按项目需要提交 Git。

## 与 wsl-devctl 的边界

- `dev-tools` 发现和生成项目版本声明。
- `wsl-devctl` 消费声明，负责 Windows 源码镜像、systemd、构建、热更新和恢复。
- 两者通过 CLI/JSON 协作，不相互导入 Python 模块。

典型流程：

```bash
dev-tools project init /mnt/e/Projects/CompanyProjects/order-service
wsl-devctl init /mnt/e/Projects/CompanyProjects/order-service \
  --toolchain mise --fix --start
```

`wsl-devctl` 支持集成参数后，也可以用一条命令完成生成和注册：

```bash
wsl-devctl init /mnt/e/Projects/CompanyProjects/order-service \
  --toolchain mise --generate-mise --fix --start
```
