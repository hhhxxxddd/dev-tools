# dev-tools

**简体中文** · [English](README.en.md)

[![License MIT](https://img.shields.io/badge/License-MIT-blue)](LICENSE) [![Windows](https://img.shields.io/badge/Platform-Windows-blue)](docs/installation.md) [![WSL Ubuntu](https://img.shields.io/badge/WSL-Ubuntu-brightgreen)](docs/installation.md) [![Python 3.14](https://img.shields.io/badge/Python-3.14-blue)](docs/installation.md) [![Version 0.5.0](https://img.shields.io/badge/Version-0.5.0-blue)](CHANGELOG.md)

为 Windows 和 WSL 开发项目提供热部署与热更新：识别依赖、准备环境、启动服务，并跟进代码和依赖变化。
支持 Node.js、Python / uv、Maven / Spring Boot 和已有 Docker Compose 项目。

## 环境要求

| 平台 / 依赖 | 版本或要求 | 使用范围 |
| --- | --- | --- |
| Windows | Windows 11 | 原生 Windows 控制程序 |
| WSL（可选） | WSL 2；已验证 Ubuntu 24.04 LTS | 管理 WSL 项目时启用 systemd |
| PowerShell | 7 或更新版本 | Windows 入口与进程管理 |
| Python | `>=3.14.8,<3.15` | 控制程序仅用标准库；可绑定已有原生解释器 |
| Git | `git` 命令可用 | 源码安装、工具更新和分支监控 |
| mise | `mise` 命令可用 | 默认宿主安装及项目运行时管理 |
| rsync | `rsync` 命令可用 | WSL 部署与源码同步 |
| Docker / Compose（可选） | Docker 与 Compose v2 | 使用 Compose 项目时需要 |
| Scoop（可选） | `scoop` 命令可用 | 使用 Scoop 安装时需要 |

项目的 Node.js、Java 等版本由根 mise 配置决定。

## 安装

### Scoop（Windows）

通过 hxd-box 安装正式发行版：

```powershell
scoop bucket add hxd-box https://github.com/hhhxxxddd/hxd-box
scoop install hxd-box/dev-tools
```

自动准备 Git、PowerShell 7、mise 和 Python 3.14 控制程序宿主。

### Git（Windows）

准备好上表中的依赖，获取并安装当前源码：

```powershell
git clone https://github.com/hhhxxxddd/dev-tools.git
cd dev-tools
.\scripts\install.ps1
```

安装后重新打开 PowerShell。绑定已有 Python、仅安装 WSL 等方式见[安装说明](docs/installation.md)。

两种安装方式均可从 Windows 部署到 WSL：

```powershell
dev-tools self update -e wsl
```

默认使用 Ubuntu；Windows 和 WSL 各自保存运行时、依赖与缓存。

## 快速开始

在项目目录执行：

```text
cd path/to/my-app
dev-tools init
dev-tools register
dev-tools start
```

`init` 保留已有配置，`start` 自动准备并启动服务。之后按配置处理热部署和依赖变化。
任务与服务写在 `dev-tools.toml`，运行时版本写在根 `mise.toml` / `.mise.toml`。

省略 `-e` 使用当前平台；加 `-e wsl` 从 Windows 管理 WSL 项目。原生 WSL 的注册和启停使用 `sudo`。

## 常用命令

| 命令 | 用途 |
| --- | --- |
| `dev-tools list --all` | 查看 Windows 与 WSL 项目 |
| `dev-tools check my-app` | 只读检查准备计划 |
| `dev-tools status my-app` | 查看项目状态 |
| `dev-tools logs my-app web --follow` | 跟踪服务日志 |
| `dev-tools stop my-app` | 停止服务与自动维护 |
| `dev-tools unregister my-app` | 取消项目注册 |
| `dev-tools config edit` | 编辑本机偏好 |
| `dev-tools upgrade` | 更新工具自身 |
| `dev-tools -v` / `dev-tools help` | 查看版本或帮助 |

在唯一匹配项目的目录中可省略项目名；替换示例中的项目和服务名。
`list` 查看当前平台，`list --all` 查看 Windows 和 WSL。
更新工具前先停止相关平台的项目，更新 WSL 使用 `dev-tools upgrade -e wsl`。
默认中文，在 `config` 中设置 `language = "en"` 切换英文。

## 文档

[安装与更新](docs/installation.md) · [项目配置](docs/project-config.md) · [运行说明](docs/usage.md) · [接口约定](docs/cli-contract.md)

[示例](examples/README.md) · [开发与贡献](docs/development.md) · [更新记录](CHANGELOG.md)
