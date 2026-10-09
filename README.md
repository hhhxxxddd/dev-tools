# dev-tools

[English](README.en.md) · [更新记录](CHANGELOG.md) · **v0.4.0**

dev-tools 是一个管理 Windows 和 WSL 开发项目的命令行工具。
它帮你发现项目依赖、准备环境，并在开发时同步源码、触发热更新或重新构建，减少切换项目和 Git 分支后的重复操作。

## 功能

- 支持 Node.js、Maven / Spring Boot、Python / uv，以及已有的 Docker Compose 项目。
- 从项目配置发现工具版本、包管理器和 Maven 模块依赖，生成可检查、可修改的开发配置。
- 管理开发服务和文件监控：前端热更新、Python 自动重载、Spring 编译与重载，以及分支切换后的重新构建。
- Windows 和 WSL 使用同一套命令，各自保存运行时、依赖和缓存。
- `list` 用表格展示项目，`status` 查看服务状态，`logs` 查看日志。
- 默认中文，可在配置中切换英文；支持 JSON 输出供脚本使用。

## 依赖发现：从已有项目开始

进入项目目录，先扫描，再预览要生成的配置：

```text
dev-tools scan
dev-tools init --dry-run
dev-tools init
```

| 项目中已有的文件 | dev-tools 会识别什么 |
|---|---|
| `mise.toml`、`.tool-versions`、`.nvmrc`、`.python-version` 等 | Node、Java、Python 等工具的版本及声明来源 |
| `package.json` 和锁文件 | npm / pnpm / Yarn / Bun、工作区、`dev` / `start` 脚本，以及常见前端框架 |
| `pom.xml` 和 Maven Wrapper 配置 | Java / Maven 版本、Spring Boot 应用、项目内模块依赖和需要一起编译的源码 |
| `pyproject.toml` 和 `uv.lock` | Python 版本要求、uv 工作区或 pip 工作流，以及 FastAPI 开发服务 |
| 已有 Compose 文件 | 容器服务入口；数据库等业务服务以项目声明为准 |

`scan` 显示工具版本、来源、冲突和缺失项；`init` 创建缺少的根级 mise 文件和 `dev-tools.toml`，已有文件会保留。混合项目可分别生成前端、Java 和 Python 任务；自动选择 Compose 的项目使用已有容器声明。

随后用 `dev-tools prepare --dry-run` 检查准备计划，再执行 `dev-tools prepare`。工具版本来自项目根 mise 文件；软件包由项目声明的 npm、Maven、uv 等任务安装，dev-tools 不替代包管理器解析第三方依赖。

扫描只读取文件。无法确定的版本、复杂的 Maven 配置、自定义启动入口或业务服务，需要你核对和补充；同优先级版本冲突会报错。Gradle 目前只支持版本扫描，需要手动声明构建和启动任务。

## 热部署：启动后如何响应改动

执行 `dev-tools start` 后，开发服务及项目声明的监控会在后台运行。Windows 直接使用源码目录；WSL 持续同步到 Linux 工作目录，依赖、虚拟环境和构建产物保留在各自平台。

| 改动 | 默认发现流程下的行为 |
|---|---|
| 前端源码 | 由 Vite / Next.js 等开发服务器处理热更新，具体能力取决于项目脚本 |
| Python / FastAPI 源码 | 由生成的 Uvicorn `--reload` 服务自动重载；需核对应用入口和依赖 |
| Spring Java 源码 | 自动编译应用及依赖模块，更新 classpath 和触发文件，由 Spring DevTools 重载应用；不是 JVM 任意代码热替换 |
| Spring 资源、新增/删除文件或构建结构 | 执行对应资源或结构构建，停止受影响服务及依赖方，完成后恢复 |
| Git 分支或 HEAD 变化 | 默认等待 Git 操作和文件稳定，重新执行准备任务与结构构建，再恢复原来运行的服务 |

例如，准备并启动项目后，可以直接修改代码或切换分支：

```text
dev-tools prepare my-app
dev-tools start my-app
git switch feature/my-change
dev-tools status my-app
dev-tools logs my-app __watch --follow
```

`feature/my-change` 是示例分支名。也可以手动触发构建：

```text
dev-tools build my-app --kind source
dev-tools build my-app --kind branch
```

热部署方式由 `dev-tools.toml` 中的启动命令和构建监控决定，不是所有服务都支持无中断更新。源码构建保持受监督进程运行；资源、结构和分支构建可能短暂停服。后台构建使用已准备的运行时，不会安装新版本或下载 Spring DevTools；工具版本或项目声明变化后，应重新执行 `prepare`。配置细节见[构建监控](docs/project-config.md#构建监控buildsservice)。

## 安装

### Windows：使用 Scoop

已有 Scoop 后，执行：

```powershell
scoop bucket add dev-tools https://github.com/hhhxxxddd/dev-tools
scoop install dev-tools/dev-tools
dev-tools help
```

Scoop 会安装 PowerShell 7、mise 和工具自身使用的 Python，无需手动配置 Python 环境。

> Scoop 安装需要已发布的 Release 和 bucket 清单。准备发布阶段请使用下面的源码安装方式。

### 需要管理 WSL 项目时

先准备一个启用 systemd 的 WSL 发行版，默认使用 Ubuntu，再从 Windows 执行一次：

```powershell
dev-tools self install -e wsl
dev-tools self status --json
```

不必进入 WSL 再克隆一次仓库。`dev-tools` 和 `dev-tools help` 会在 WSL 部署缺失时提示安装。
其他发行版通过 `dev-tools config edit` 修改 `[wsl].distro`。Debian / Ubuntu 可自动安装缺少的 mise 和 rsync；其他发行版须先安装这两个工具。

### 从源码安装

需要 PowerShell 7、Git 和已有的 WSL；Windows 缺少 mise 时还需要 Scoop。在 PowerShell 执行：

```powershell
git clone https://github.com/hhhxxxddd/dev-tools.git
cd dev-tools
.\scripts\bootstrap.ps1
. $PROFILE
dev-tools help
```

bootstrap 会安装 Windows 和 WSL 两端，已完成这一步就无需再次部署 WSL。
其他发行版可用 `.\scripts\bootstrap.ps1 -Distro <发行版名>`，同时修改本机配置中的发行版设置。
只安装单端或手动安装的步骤见[安装说明](docs/installation.md)。

## 快速上手

在项目目录中执行：

```text
cd path/to/my-app
dev-tools scan
dev-tools init --dry-run
dev-tools init
dev-tools register
dev-tools prepare --dry-run
dev-tools prepare
dev-tools start
dev-tools status
```

`scan` 检查版本声明，`init` 创建缺少的配置，已有配置会保留。先检查结果和配置，再用 `prepare` 安装项目工具和依赖，最后用 `start` 启动服务。扫描和初始化不会运行项目代码或安装运行时。

省略 `-e` 时使用当前平台。从 Windows 控制 WSL 项目，可以这样执行：

```powershell
dev-tools -e wsl register .
dev-tools -e wsl prepare my-app
dev-tools -e wsl start my-app
dev-tools -e wsl list
```

原生 WSL 中，注册、准备和启停等修改操作需要 `sudo`；只读操作和准备预览不需要：

```bash
sudo dev-tools prepare my-app
sudo dev-tools start my-app
dev-tools list
```

把 `my-app` 换成实际注册名。在项目目录或子目录中，能唯一匹配已注册项目时可省略名称。

## 常用命令

| 命令 | 用途 |
|---|---|
| `dev-tools scan` | 查看项目需要的工具版本 |
| `dev-tools init` | 创建缺少的项目配置 |
| `dev-tools register` | 把当前项目加入本机管理 |
| `dev-tools prepare my-app` | 安装项目运行时，准备依赖和构建产物 |
| `dev-tools start my-app` | 启动项目服务 |
| `dev-tools stop my-app` | 停止项目服务 |
| `dev-tools restart my-app` | 重启项目服务 |
| `dev-tools build my-app --kind branch` | 手动执行分支构建并恢复服务 |
| `dev-tools list` | 列出当前平台的项目与状态 |
| `dev-tools status my-app` | 查看项目和服务状态 |
| `dev-tools logs my-app web --follow` | 持续查看 web 服务日志 |
| `dev-tools config edit` | 编辑本机偏好 |
| `dev-tools help` | 查看全部命令 |

把 `web` 换成项目配置中的服务名。更多参数和 JSON 输出示例：

```text
dev-tools help prepare
dev-tools list --json
dev-tools sysinfo
```

## 配置

| 文件 | 用途 |
|---|---|
| 项目根目录的 `dev-tools.toml` | 如何安装依赖、构建和启动服务 |
| 项目根目录的 `mise.toml` / `.mise.toml` | 项目需要的工具版本 |
| 本机用户的 `config.toml` | 语言、编辑器、WSL 发行版等偏好 |

用 `dev-tools config edit` 打开本机配置，例如：

```toml
language = "zh"

[wsl]
distro = "Ubuntu"
```

语言支持 `zh` / `en`，下次调用生效。项目配置可以提交到 Git，本机配置由各平台各自保存。
完整字段见[项目配置](docs/project-config.md)和[配置与运行说明](docs/usage.md)。

## 更新与卸载

更新前，先停止目标平台中正在运行的项目。Scoop 安装的版本分别更新两端：

```powershell
dev-tools -e win stop my-app
scoop update dev-tools
dev-tools -e wsl stop my-app
dev-tools self update -e wsl
```

更新后按需重新 `start`。源码安装先 `git pull --ff-only`，再运行对应安装器。用户配置、项目注册、缓存和项目运行时会保留。

Windows 使用 `scoop uninstall dev-tools` 卸载 Scoop 安装的入口；WSL 卸载步骤见[安装说明](docs/installation.md)。卸载 Windows 端不会卸载 WSL 端。

## 文档

- [安装、更新与发行](docs/installation.md)
- [项目配置参考](docs/project-config.md)
- [配置、报告与运行说明](docs/usage.md)
- [项目示例](examples/README.md)
- [开发与验证](docs/development.md)

## 参与开发

欢迎提交 Issue 或 Pull Request。控制程序使用 Python 3.14.8，没有第三方 Python 运行依赖。
修改前请阅读[开发说明](docs/development.md)和[维护约束](AGENTS.md)。

## 许可证

[MIT](LICENSE)
