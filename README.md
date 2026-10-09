# dev-tools

[English](README.en.md) · [更新记录](CHANGELOG.md) · **v0.4.0**

**自动发现项目依赖，改代码后热部署，升级依赖后继续开发。**

dev-tools 是面向 Windows 和 WSL 的开发项目管理工具，适合让 AI 反复改代码、加依赖、切分支的 **vibe coding** 工作流。把环境准备和服务管理交给工具，让你更快看到修改后的效果。

## 核心能力

- **自动发现依赖**：读取项目已有的版本、包管理器和构建声明，识别运行时与 Maven 模块依赖，生成可检查的开发配置。
- **改代码后热部署**：前端使用开发服务器热更新，Python 自动重载，Spring 自动编译并通过 DevTools 重载；切换分支后按配置重新构建。
- **依赖升级后接着开发**：更新依赖声明、锁文件及必要的工具版本配置，再执行 `prepare`，沿用原注册和工作目录重新准备环境，成功后恢复原来运行的服务。

支持 Node.js、Maven / Spring Boot、Python / uv 和已有 Docker Compose 项目。Windows 与 WSL 使用相同命令，各自保存运行时、依赖和缓存。

## 安装

目前可从源码安装。在 PowerShell 中执行：

```powershell
git clone https://github.com/hhhxxxddd/dev-tools.git
cd dev-tools
.\scripts\bootstrap.ps1
. $PROFILE
dev-tools help
```

需要 PowerShell 7、Git 和已启用 systemd 的 WSL，默认使用 Ubuntu；Windows 缺少 mise 时需要 Scoop。bootstrap 会部署 Windows 和 WSL 两端。

正式 Release 和 bucket 清单发布后，也可以使用 Scoop：

```powershell
scoop bucket add dev-tools https://github.com/hhhxxxddd/dev-tools
scoop install dev-tools/dev-tools
```

Scoop 准备 Windows 端及工具自身的 Python。需要 WSL 时，从 Windows 部署一次：

```powershell
dev-tools self install -e wsl
```

源码 bootstrap 已完成 WSL 部署时可跳过。无需进入 WSL 再克隆仓库；`dev-tools` 和 `dev-tools help` 会提示缺失的 WSL 部署。单端安装、其他发行版和更新步骤见[安装说明](docs/installation.md)。

## 快速上手

进入已有项目目录：

```text
cd path/to/my-app
dev-tools scan
dev-tools init --dry-run
dev-tools init
dev-tools register
dev-tools prepare --dry-run
dev-tools prepare
dev-tools start
```

先检查扫描结果和生成配置，再准备并启动项目。`init` 保留已有配置；`scan` 和 `init` 不执行项目代码或安装运行时。缺失版本或自定义入口需要补充到项目配置。

省略 `-e` 使用当前平台。从 Windows 控制 WSL 项目：

```powershell
dev-tools -e wsl register .
dev-tools -e wsl prepare my-app
dev-tools -e wsl start my-app
```

`my-app` 替换为实际注册名；在能唯一匹配项目的目录中可省略。原生 WSL 的注册、准备和启停操作需要 `sudo`，只读命令与准备预览不需要。

## 持续开发

启动后，直接改代码或切换 Git 分支，工具会按项目配置同步源码、触发重载或重新构建。依赖升级后无需重新注册项目，更新声明和锁文件后执行：

```text
dev-tools prepare my-app --dry-run
dev-tools prepare my-app
dev-tools status my-app
```

运行时版本写在项目根 `mise.toml` / `.mise.toml`，安装、构建和启动流程写在 `dev-tools.toml`。热部署能力取决于框架和项目配置，部分重建会短暂停服；新增运行时版本由显式 `prepare` 安装。

## 常用命令

| 命令 | 用途 |
|---|---|
| `dev-tools list` | 表格查看项目及状态 |
| `dev-tools status my-app` | 查看服务状态 |
| `dev-tools logs my-app web --follow` | 持续查看服务日志，web 为实际服务名 |
| `dev-tools build my-app --kind branch` | 手动触发完整分支构建 |
| `dev-tools stop my-app` | 停止项目 |
| `dev-tools help` | 查看全部命令 |

默认中文，通过 `dev-tools config edit` 设置 `language = "en"` 可切换英文；本机偏好由两端分别保存。

## 文档与贡献

- [安装、更新与卸载](docs/installation.md)
- [项目配置与热部署策略](docs/project-config.md)
- [配置、报告与运行说明](docs/usage.md)
- [项目示例](examples/README.md)
- [开发与验证](docs/development.md)

欢迎提交 Issue 和 Pull Request。开发前请阅读[维护约束](AGENTS.md)。

## 许可证

[MIT](LICENSE)
