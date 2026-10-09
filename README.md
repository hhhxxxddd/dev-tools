# dev-tools

[English](README.en.md) · [更新记录](CHANGELOG.md) · [v0.4.1](https://github.com/hhhxxxddd/dev-tools/releases/tag/v0.4.1)

**近乎无感的本地开发部署：自动发现依赖，改代码热部署，依赖升级后接着开发。**

面向 Windows 和 WSL 的开发项目管理工具，适合让 AI 反复改代码、加依赖、切分支的 **vibe coding** 工作流。注册并启动一次，之后专心改代码，依赖同步和服务恢复交给 dev-tools。

- **自动发现**：读取项目已有声明，识别 Node.js、Maven / Spring Boot、Python / uv 和已有 Docker Compose 项目，生成可检查的开发配置。
- **热部署**：前端热更新、Python 自动重载、Spring 编译与 DevTools 重载；切换分支后按配置重新构建。
- **跟进依赖变化**：运行期间按模块同步锁文件、安装新增或升级的包、清理移除的依赖并恢复服务。无需重新注册或手动准备，保留手改命令。

## 安装

Windows 推荐通过 Scoop 安装：

```powershell
scoop bucket add dev-tools https://github.com/hhhxxxddd/dev-tools
scoop install dev-tools/dev-tools
dev-tools help
```

Scoop 安装 Git、PowerShell 7、mise 和控制程序自己的 Python。项目运行时由项目根级版本声明决定，Windows 与 WSL 各自保存依赖和缓存。

需要 WSL 时，先安装发行版并启用 systemd，再从 Windows 部署一次：

```powershell
dev-tools self update -e wsl
```

首次部署和后续更新使用同一命令，无需进入 WSL 再克隆仓库。默认发行版为 Ubuntu；进入 `dev-tools` 或 `help` 会提示缺失部署。源码安装和其他发行版见[安装说明](docs/installation.md)。

## 快速上手

进入已有项目目录：

```text
cd path/to/my-app
dev-tools init
dev-tools register
dev-tools start
```

`init` 保留已有配置，缺少明确版本或自定义入口时会提示补充；`start` 自动准备环境。随后直接改代码、增删或升级依赖、切换分支即可。依赖更新可能短暂停服，热部署取决于框架和配置。

省略 `-e` 使用当前平台；从 Windows 控制 WSL 项目：

```powershell
dev-tools -e wsl register .
dev-tools -e wsl start
```

原生 WSL 的注册和启停需要 `sudo`。`stop` 停止自动维护，下次 `start` 补齐变化。

## 日常使用

```text
dev-tools list
dev-tools status my-app
dev-tools logs my-app web --follow
dev-tools stop my-app
```

替换示例项目名和服务名；在唯一匹配项目的目录中可省略项目名。预览准备计划用 `dev-tools prepare --dry-run`，手动完整构建用 `dev-tools build`。

流程写在 `dev-tools.toml`，运行时版本写在根 `mise.toml` / `.mise.toml`；扫描和初始化不执行项目代码或安装运行时。`dependency_mode = "locked"` 可禁止自动修改锁文件。默认中文，通过 `dev-tools config edit` 设置 `language = "en"` 切换英文。

## 更新

先停止对应平台的项目，再更新：

```powershell
scoop update
scoop update dev-tools
dev-tools self update -e wsl
```

WSL 更新按需执行。两端保留项目注册、偏好、缓存和项目运行时，更新后用 `start` 恢复服务。

## 文档与贡献

[安装与卸载](docs/installation.md) · [项目配置与热部署](docs/project-config.md) · [运行说明](docs/usage.md) · [示例](examples/README.md)

欢迎提交 Issue 和 Pull Request；开发前请阅读[开发与验证](docs/development.md)和[维护约束](AGENTS.md)。

[MIT](LICENSE)
