# dev-tools

**简体中文** · [English](README.en.md)

[![License MIT](https://img.shields.io/badge/License-MIT-blue)](LICENSE) [![Windows](https://img.shields.io/badge/Windows-11-blue)](docs/installation.md) [![WSL Ubuntu](https://img.shields.io/badge/WSL-Ubuntu-brightgreen)](docs/installation.md) [![Python 3.14](https://img.shields.io/badge/Python-3.14-blue)](docs/installation.md) [![Version 0.5.6](https://img.shields.io/badge/Version-0.5.6-blue)](CHANGELOG.md)

Windows / WSL 开发项目的热部署与热更新工具。

按项目声明准备运行时和依赖、启动服务；运行期间跟进源码、依赖和配置变化，执行构建或恢复受影响的服务。

## 演示

https://github.com/user-attachments/assets/e900a509-ee51-41ab-9b7c-e11007f653cb

42 秒操作演示：启动项目 → 修改源码 → 更新依赖 → 跨端查看日志 → 定位端口。

[下载 MP4](docs/assets/dev-tools-demo.zh.mp4) · [查看截图](docs/assets/dev-tools-demo.zh.png)

基于隔离样例实际运行结果制作，终端界面重绘，输出节选并压缩等待时间。样例使用声明的构建任务与 Python HTTP 服务；热更新方式由项目配置和框架决定。

## 安装

### Scoop

在 Windows PowerShell 中执行：

```powershell
scoop bucket add hxd-box https://github.com/hhhxxxddd/hxd-box
scoop install hxd-box/dev-tools
```

安装正式发行版，并准备 Git、PowerShell 7、mise 和 Python 3.14 控制程序宿主。

### Git

准备下表中的依赖后，在 PowerShell 7 中执行：

```powershell
git clone https://github.com/hhhxxxddd/dev-tools.git
cd dev-tools
.\scripts\install.ps1
```

安装后重新打开终端。已有 Python 绑定与原生 WSL 安装见[安装说明](docs/installation.md)。

需要管理 WSL 项目时，从 Windows 部署到已启用 systemd 的 WSL 发行版：

```powershell
dev-tools self update -e wsl
```

默认发行版为 Ubuntu，可在本机配置中修改。

## 开始使用

在需要管理的项目根目录中执行：

```powershell
dev-tools init
dev-tools register
dev-tools start
```

`init` 生成缺少的项目配置，保留已有文件；`register` 建立本机绑定；`start` 准备环境并启动服务与监控。

源码变化按声明触发热重载或构建；依赖、锁文件和配置变化触发准备与服务恢复。部分变更需要短暂停服，具体行为由项目声明决定。

省略 `-e` 使用当前平台。Windows 与 WSL 均可用 `-e win` / `-e wsl` 指定目标，例如：

```powershell
dev-tools register -e wsl
dev-tools start -e wsl
dev-tools list --all
```

原生 WSL 终端中的注册、启停等控制操作需要 `sudo`。入口无法自动确定时，先补充项目配置，再启动。

## 支持的项目

| 项目类型 | 默认识别与配置生成 |
| --- | --- |
| Node.js / 前端 | npm、pnpm、Yarn、Bun；识别 Next.js、Vite、React，生成依赖准备与 dev/start 服务 |
| Python | 读取 pyproject.toml，生成 uv 或 pip 准备流程；FastAPI 可生成 Uvicorn 热重载服务 |
| Java / Maven | 识别多模块、Maven Wrapper 与 Spring Boot，生成准备和热部署流程 |
| Docker Compose | 使用已有 Compose 声明；自动发现时优先采用 Compose |
| Rust / Tauri、Gradle、自定义进程 | 支持版本元数据扫描；任务与启动命令需手动配置 |

支持多语言项目与声明的工作区、模块。完整字段和示例见[项目配置](docs/project-config.md)与[示例目录](examples/README.md)。

## 常用操作

| 命令 | 用途 |
| --- | --- |
| `dev-tools list --all` | 查看 Windows / WSL 两端项目、端口与状态 |
| `dev-tools check my-app` | 只读检查准备计划和阻塞原因 |
| `dev-tools status my-app -e wsl` | 查看 WSL 项目的服务与健康状态 |
| `dev-tools logs my-app api -e wsl --follow` | 持续查看指定项目的 api 服务日志 |
| `dev-tools stop my-app` | 停止服务与自动维护 |
| `dev-tools unregister my-app` | 停止并注销；默认保留源码、缓存和运行时 |
| `dev-tools upgrade` | 获取新版，确认后停止并恢复受影响项目 |
| `dev-tools -v` / `dev-tools help` | 查看版本或帮助 |

在唯一匹配的已注册项目目录中可省略项目名。`logs my-app` 汇总项目日志，`logs my-app api` 指定服务，`logs my-app --task` 查看任务日志。

### 工具箱

| 命令 | 用途 |
| --- | --- |
| `dev-tools ports show 3000` | 定位监听端口、进程和可确认的项目归属 |
| `dev-tools which python -e wsl` | 查看 WSL PATH 中的命令候选与链接落点 |
| `dev-tools net check http://localhost:3000/health` | 诊断 DNS、TCP、TLS 与 HTTP 连接 |
| `dev-tools ssh list --edit` | 交互编辑 SSH 清单 |
| `dev-tools hosts list --edit -e win` | 交互编辑 Windows hosts |

SSH、端口、PATH 与网络工具均支持 Windows / WSL；hosts 仅管理 Windows。单独输入工具箱命令显示帮助；文本编辑器入口 `ssh edit`、`hosts edit` 保留。

## 运行方式与配置

Windows 直接使用源码目录；WSL 将源码同步到 Linux 原生工作目录，依赖、构建和服务运行留在 Linux 中。两端共享项目声明，各自维护运行时、依赖、缓存与本机注册。

| 配置 | 内容 |
| --- | --- |
| 项目根目录的 `dev-tools.toml` | 任务、服务、健康检查、构建与同步策略 |
| 项目根目录的 `mise.toml` / `.mise.toml` | 项目运行时版本 |
| 各端用户的 `config.toml` | dev-tools 的语言、编辑器、WSL 发行版、系统信息与报告偏好 |

```powershell
dev-tools config edit
dev-tools config check
```

默认中文；在本机配置中设置 `language = "en"` 切换英文。编辑器依次使用已配置的命令、VISUAL、EDITOR，最后使用 Windows 记事本 / WSL vi。

扫描不读取 `.gitignore`，范围由工作流、工作区和 `[scan]` 配置决定。`scan`、`check` 只读；运行时安装仅采用项目根 mise 声明。中间件由项目自己的 Compose 或外部环境提供。

## 环境要求

| 平台 / 依赖 | 要求 |
| --- | --- |
| Windows | Windows 11、PowerShell 7 或更新版本 |
| WSL（可选） | WSL 2，已验证 Ubuntu 24.04 LTS；启用 systemd，具备 rsync |
| Python | `>=3.14.8,<3.15`；控制程序仅用标准库，可绑定已有原生解释器 |
| Git / mise | Git 用于源码安装与更新；mise 用于默认宿主安装和项目运行时管理 |
| Docker（可选） | 使用 Compose 项目时需要 Docker 与 Compose v2 |

项目所需 Node.js、Java 等版本由项目根 mise 配置决定，与控制程序宿主分开。

## 文档

[安装与更新](docs/installation.md) · [项目配置](docs/project-config.md) · [运行说明](docs/usage.md) · [示例](examples/README.md)

[CLI 与自动化接口](docs/cli-contract.md) · [开发与贡献](docs/development.md) · [更新记录](CHANGELOG.md) · [MIT 许可](LICENSE)
