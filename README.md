# dev-tools

**简体中文** · [English](README.en.md)

[![License MIT](https://img.shields.io/badge/License-MIT-blue)](LICENSE) [![Windows](https://img.shields.io/badge/Platform-Windows-blue)](docs/installation.md) [![WSL Ubuntu](https://img.shields.io/badge/WSL-Ubuntu-brightgreen)](docs/installation.md) [![Python 3.14](https://img.shields.io/badge/Python-3.14-blue)](docs/installation.md) [![Version 0.5.2](https://img.shields.io/badge/Version-0.5.2-blue)](CHANGELOG.md)

为 Windows 和 WSL 项目提供近乎无感的开发部署体验：自动发现依赖、准备环境、启动服务，持续跟进代码和依赖声明的变化。
依赖新增、删除或升级后，按项目配置自动同步并继续开发，适合频繁调整项目的 Vibe Coding。
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

省略 `-e` 使用当前平台；加 `-e win` / `-e wsl` 可从任意一端操作目标环境，项目、SSH、配置、系统信息、报告和工具自身维护使用相同写法。原生 WSL 的注册和启停使用 `sudo`。

## 常用命令

| 命令 | 用途 |
| --- | --- |
| `dev-tools list` | 查看当前环境项目；`--all` 查看两端 |
| `dev-tools check my-app` | 只读检查准备计划 |
| `dev-tools status my-app` | 查看项目状态 |
| `dev-tools logs my-app web --follow` | 跟踪服务日志 |
| `dev-tools stop my-app` | 停止服务与自动维护 |
| `dev-tools unregister my-app` | 取消项目注册 |
| `dev-tools config edit` | 编辑本机偏好 |
| `dev-tools ssh list` | 查看 SSH 清单；show 查看来源、edit 编辑、connect 连接 |
| `dev-tools hosts list` | 查看 Windows hosts、启用状态与冲突 |
| `dev-tools upgrade` | 更新工具自身 |
| `dev-tools -v` / `dev-tools help` | 查看版本或帮助 |

在唯一匹配项目的目录中可省略项目名；替换示例中的项目和服务名。
`list` 默认只查当前环境；加 `-e win` 或 `-e wsl` 查询指定侧，`list --all` 汇总两端。列为名称、环境、端口、类型、状态。
终端中错误为红色、提示为黄色；运行中为绿色，待恢复为黄色，已停止为灰色。`start` 显示阶段和耗时，长任务每 5 秒更新提示。
`start --all` 启动当前端或指定端的全部项目，跳过已有活动 worker 的项目。
更新工具时提示停止受影响的运行项目，输入 `Y` 后更新并恢复；脚本可加 `--yes`。`dev-tools upgrade -e wsl` 升级 WSL。
`config edit -e win` 编辑 Windows 偏好，`sysinfo -e wsl` 查询 WSL，`self status -e win` 查看 Windows 部署。跨端执行读取目标侧配置；两端安装与缓存保持独立。`hosts` 的实际操作目前只支持 Windows。
默认中文，在 `config` 中设置 `language = "en"` 切换英文。
直接输入 `dev-tools ssh` 或 `dev-tools hosts` 显示各自帮助，等同于加 `-h`。

SSH 直接使用已有配置，支持查看、定位编辑和原生连接，不需要注册项目：

```text
dev-tools ssh list
dev-tools ssh show my-server
dev-tools ssh edit my-server
dev-tools ssh connect my-server -e wsl
```

`list` 支持关键词过滤，`list/show` 支持 `--json`，复杂条件标为“待确认”。`edit` 定位声明文件，省略别名时编辑用户配置；连接使用目标侧原生 SSH。查询保持只读，密钥和配置各端独立。详见 [SSH 用法](docs/usage.md#ssh)。

Windows hosts 支持直接编辑，也可以保存并切换开发方案：

```text
dev-tools hosts edit             # 编辑当前 hosts
dev-tools hosts edit dev         # 创建或编辑 dev 方案
dev-tools hosts use dev          # 启用方案，仅替换托管区块
dev-tools hosts off              # 停用方案，保留手工条目
dev-tools hosts restore          # 恢复最近匹配的备份
```

编辑后保存、关闭编辑器，再回终端按 Enter。查看与方案编辑不提权，系统写入按需请求 Windows 管理员权限并自动备份。`hosts profiles` 查看方案，`hosts backups` 查看备份；从 WSL 操作须显式加 `-e win`。详见[运行说明](docs/usage.md#windows-hosts)。

## 文档

[安装与更新](docs/installation.md) · [项目配置](docs/project-config.md) · [运行说明](docs/usage.md) · [接口约定](docs/cli-contract.md)

[示例](examples/README.md) · [开发与贡献](docs/development.md) · [更新记录](CHANGELOG.md)
