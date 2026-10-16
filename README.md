# dev-tools

**简体中文** · [English](README.en.md)

[![License MIT](https://img.shields.io/badge/License-MIT-blue)](LICENSE) [![Windows](https://img.shields.io/badge/Platform-Windows-blue)](docs/installation.md) [![WSL Ubuntu](https://img.shields.io/badge/WSL-Ubuntu-brightgreen)](docs/installation.md) [![Python 3.14](https://img.shields.io/badge/Python-3.14-blue)](docs/installation.md) [![Version 0.5.5](https://img.shields.io/badge/Version-0.5.5-blue)](CHANGELOG.md)

为 Windows 和 WSL 项目提供近乎无感的开发部署体验：自动发现依赖、准备环境、启动服务，持续跟进代码和依赖声明的变化。
依赖新增、删除或升级后，按项目配置自动同步并继续开发，适合频繁调整项目的 Vibe Coding。
空闲时自动降低扫描频率，发现变化后恢复快速同步；WSL 的依赖与编译留在 Linux 原生目录。

## 支持的项目

| 项目类型 | 默认支持 |
| --- | --- |
| Node.js / 前端 | 从 package.json 生成依赖准备与 dev/start 服务；支持 npm、pnpm、Yarn、Bun，识别 Next.js、Vite、React |
| Python | 从 pyproject.toml 生成 uv 或 pip 准备流程；FastAPI 项目可生成 Uvicorn 热重载服务 |
| Java / Maven | 识别多模块、Maven Wrapper 与 Spring Boot，生成准备和热部署流程 |
| Docker Compose | 使用已有 Compose 声明；自动发现时优先采用 Compose |
| Rust / Tauri、Gradle、自定义进程 | 手写任务和启动命令；支持 Rust 工具链、Cargo 工作区与最低版本校验，Gradle 支持 JDK 元数据扫描 |

可组合多语言项目；声明的工作区和模块会参与扫描。桌面客户端与共享服务见[桌面模板](examples/dev-desktop.toml)，其他配置见[项目示例](examples/README.md)。运行时版本仍以项目根级 mise 配置为准，无法确定的入口需手动配置。

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
| `dev-tools ports list` | 查看实际监听端口及所属进程；`show 3000` 定位占用 |
| `dev-tools which python` | 查看目标端 PATH 中的命令候选和链接落点 |
| `dev-tools net info` | 查看 DNS、环境代理及 WSL 网络；`check 主机:端口` 诊断连接 |
| `dev-tools upgrade` | 更新工具自身 |
| `dev-tools -v` / `dev-tools help` | 查看版本或帮助 |

在唯一匹配项目的目录中可省略项目名。`list --all` 汇总两端；`start --all` 启动所选环境中尚未运行的项目。
Windows 查询 WSL 的 `list/status` 时还会检查 localhost 访问；常见 Web 服务会等待 HTTP 响应，转发卡住时显示“Windows访问异常”。
工具箱命令 `ssh`、`hosts`、`ports`、`which`、`net` 单独运行均显示帮助，具体操作见[运行说明](docs/usage.md)。

```text
dev-tools ports show 3000 -e win
dev-tools which python -e wsl
dev-tools net check http://localhost:3000/health -e wsl
```

端口与 PATH 查询只读；网络诊断使用直连，分别检查 DNS、TCP、TLS、HTTP。`hosts` 仅管理 Windows，系统写入按需提权并自动备份。
更新时输入 `Y`，或加 `--yes`，停止受影响项目并在更新后恢复；`dev-tools upgrade -e wsl` 升级 WSL。
默认中文，通过 `dev-tools config edit` 设置 `language = "en"` 切换英文；配置与缓存各端独立。
扫描不读取 `.gitignore`；按工作流和工作区确定范围，可在项目配置的 `[scan]` 中指定发现起点、排除路径和深度，详见[项目配置](docs/project-config.md#扫描范围与版本校验)。
Python、Vite 与可关闭重开的桌面客户端可共用一个生命周期，见[桌面模板](examples/dev-desktop.toml)。

## 文档

[安装与更新](docs/installation.md) · [项目配置](docs/project-config.md) · [运行说明](docs/usage.md) · [接口约定](docs/cli-contract.md)

[示例](examples/README.md) · [开发与贡献](docs/development.md) · [更新记录](CHANGELOG.md)
