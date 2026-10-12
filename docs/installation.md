# 安装、更新与发行

[English](installation.en.md)

## Scoop 管理 Windows

Scoop 清单由 [hxd-box](https://github.com/hhhxxxddd/hxd-box) 维护，跟随正式 Release。Git 安装使用当前源码。

```powershell
scoop bucket add hxd-box https://github.com/hhhxxxddd/hxd-box
scoop install hxd-box/dev-tools
dev-tools help
scoop update dev-tools
scoop uninstall dev-tools
```

清单自动安装 `main/git`、`main/pwsh`、`main/mise` 和 Python 3.14.8 控制程序宿主，创建 `dev-tools` shim。无需 Git 克隆或 profile 函数。旧安装器写入的 dev-tools 标记块会被移除；已有终端需重新打开以清除已加载的旧函数。

更新和卸载前须停止活动的 Windows 项目，例如 `dev-tools -e win stop my-app`。钩子校验 PID 与启动时间，拒绝移除活动 worker 使用的程序。用户偏好、注册、状态、缓存和项目运行时保留；WSL 不随 Scoop 操作更新或卸载。

## WSL 部署与更新

Scoop 安装后，从 Windows 部署一次，后续显式更新：

```powershell
dev-tools self update -e wsl
dev-tools self status --json
```

`self update` 同时用于首次部署和更新，可重复执行；将本机包的同版本部署到配置的 `[wsl].distro`，无需进入 Linux 克隆仓库。先停止目标发行版中的项目，例如 `dev-tools -e wsl stop my-app`，完成后按需 `start`。安装器拒绝覆盖活动 worker 使用的文件，保留注册、偏好、状态、缓存和项目运行时，不自动准备或启动项目。

发行版须已安装并启用 systemd。缺少 rsync，或首次部署未绑定外部 Python 且缺少 mise 时，Debian/Ubuntu 通过 extrepo/APT 安装；其他发行版须先自行安装工具。Linux 程序位于 `/opt/dev-tools`，入口为 `/usr/local/bin/dev-tools`。未指定外部解释器的首次安装会在 `/opt/dev-tools/host-mise` 准备 Python 宿主；后续部署沿用 `.host-python` 绑定。不共享 Windows 可执行目录和缓存。

`dev-tools`、`dev-tools help` 和顶层 `--help` 共用总览，最多 5 秒的只读文件探测检查 WSL 入口、源码和宿主。缺失或不完整时提示部署；WSL 不可用、发行版配置错误或超时时提示检查发行版。帮助不安装、升级或执行 WSL 中的 dev-tools；单个命令的帮助不执行探测。

原生 WSL 可获取新版，或重新部署安装时记录的源码：

```bash
sudo dev-tools upgrade -e wsl
sudo dev-tools self update -e wsl --source /path/to/dev-tools
```

`self update` 不获取新版；原生 WSL 没有记录的源码目录时，须通过 `--source` 指定当前检出目录或发行包。也可从 Windows 已安装的包部署。来源须是 origin 为官方仓库的检出目录，或发行流程生成的包。Windows 的 `self update -e win` 提示使用 Scoop。`self status --json` 输出稳定字段，不自动同步版本。

### 直接升级工具

`dev-tools -v` / `--version` 只输出工具版本。`dev-tools upgrade` 获取新版并部署当前平台，`-e wsl` 选择 WSL；`dev-tools self upgrade` 是同一操作的另一入口，接受相同的 `-e` 和 `--source` 参数。Windows 的 Scoop 安装使用 Scoop；源码安装使用已验证的 Git origin，拒绝本地修改、脱离分支、未跟踪 origin 或分叉的历史，只允许快进合并。Windows 升级前须停止 Windows 项目，WSL 部署前须停止 WSL 项目；WSL 升级若会更新 Windows 控制程序正在使用的同一份源码仓库，也须先停止 Windows 项目。

原生 WSL 的 `upgrade` 优先使用安装器记录的 `.source` 仓库；没有可用 Git 仓库时下载最新稳定 Release，验证 SHA256SUMS 和包内逐文件校验清单后部署。升级时显式 `--source` 要求可信 Git 仓库。源码安装可执行 `dev-tools upgrade -e win`，无需先手动拉取；也可用 `dev-tools upgrade --source <仓库目录>` 指定来源。`self update` 重新部署当前代码，不拉取源码或下载 Release；记录的源码不可用时显式提供 `--source`。

## 源码安装

仍支持 `scripts/bootstrap.ps1` 同时部署两端、`scripts/install.ps1` 安装 Windows profile 入口、`sudo bash scripts/install.sh` 部署 WSL。bootstrap 与 Windows 到 WSL 的部署助手先验证 Git origin 再执行安装器。先 `git pull --ff-only`，再运行安装器或 `self update -e wsl` 更新部署。

### 使用外部 Python

源码安装按 `DEV_TOOLS_PYTHON`、已有本平台绑定、默认 mise 宿主的顺序选择 Python。Windows 使用 `.host-python.win`，WSL 使用 `.host-python`，共享源码目录时两者互不读取。解释器必须属于目标平台，版本为 `>=3.14.8,<3.15`，并提供 SSL 和 TOML 标准库。显式路径或已有绑定失效时安装失败，不会改用其他 Python。安装器保存解释器的真实绝对路径；修改一个 `current` 链接不会自动切换绑定。

Windows 使用本机已有解释器：

```powershell
$env:DEV_TOOLS_PYTHON = (Get-Command python).Source
.\scripts\install.ps1
Remove-Item Env:DEV_TOOLS_PYTHON
```

WSL 使用由管理员准备的共享解释器：

```bash
sudo env DEV_TOOLS_PYTHON=/opt/dev-runtimes/python/3.14.8/bin/python3 bash scripts/install.sh
```

从 Windows 部署时，`scripts/install-wsl.ps1 -Python /opt/dev-runtimes/python/3.14.8/bin/python3` 指定目标 WSL 的路径；不会将 Windows 的解释器绑定传入 WSL。

外部模式不安装或升级 Python，也不要求 mise 来启动控制器；项目使用 mise 时仍须准备 mise。外部解释器由管理员负责维护，dev-tools 更新和卸载不删除它。项目可用 `mise link python@3.14.8 /opt/dev-runtimes/python/3.14.8` 引用同一份基础安装，版本仍由项目根 mise 文件声明，各项目依赖留在独立 `.venv`。基础安装应由 root 持有，所有使用者可读取和执行；不要向共享基础安装添加项目依赖。

## 发行维护

控制程序、pyproject 和 tag 的版本必须一致。先构建并验证本地候选包：

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m dev_tools.validation
python -m dev_tools.release --output dist
python -m dev_tools.package_smoke dist/dev-tools-0.5.1.zip
```

产物为 `dev-tools-<版本>.zip`、`SHA256SUMS` 和 `dev-tools.json`。完整 ZIP 包含两端入口、配置、示例与文档，排除本机绑定、缓存与本地覆盖。`.release.json` 记录逐文件哈希、版本、源码提交及 dirty 标记。SHA256 用于确认字节完整性；来源真实性依赖从官方 GitHub Release 或 Scoop 渠道取得包与校验值。

给已提交、干净的源码创建 `v<版本>` tag 后，`.github/workflows/release.yml` 复用 Windows/Ubuntu CI，要求正式构建匹配 tag，验证解压入口并逐字节比较两端产物。`docs/releases/v<版本>.md` 必须存在。仅发布任务拥有仓库写权限。

已有草稿可接收验证过的附件后发布；已有正式 Release 只核对附件，绝不覆盖。普通分支的手动运行只验证候选，不发布。每次正式 Release 生成 `dev-tools.json` 附件，供 hxd-box 同步维护 bucket 清单；本仓库的发行工作流不自动向 bucket 推送更新。

源码部署验证 Git origin，发行包部署校验完整清单后才执行安装器。部署保留外部 Python、注册、偏好和状态。创建 tag 前仍须通过真实 WSL 生命周期与双向传输检查，Ubuntu CI 不能覆盖 Windows↔WSL 行为。
