# 安装、更新与发行

[English](installation.en.md)

## Scoop 管理 Windows

首次 Release 和 bucket 清单发布后：

```powershell
scoop bucket add dev-tools https://github.com/hhhxxxddd/dev-tools
scoop install dev-tools/dev-tools
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

发行版须已安装并启用 systemd。缺少 mise/rsync 时，Debian/Ubuntu 通过 extrepo/APT 安装；其他发行版须先自行安装工具。Linux 程序位于 `/opt/dev-tools`，控制程序宿主位于 `/opt/dev-tools/host-mise`，入口为 `/usr/local/bin/dev-tools`。不共享 Windows 可执行目录和缓存。

`dev-tools`、`dev-tools help` 和顶层 `--help` 共用总览，最多 5 秒的只读文件探测检查 WSL 入口、源码和宿主。缺失或不完整时提示部署；WSL 不可用、发行版配置错误或超时时提示检查发行版。帮助不安装、升级或执行 WSL 中的 dev-tools；单个命令的帮助不执行探测。

原生 WSL 可更新最新稳定 Release，验证该 Release 的 SHA256SUMS 和包内逐文件校验清单后部署：

```bash
sudo dev-tools self update -e wsl
sudo dev-tools self update -e wsl --source /path/to/dev-tools
```

首次 Release 尚未发布时，从 Windows 部署或提供 `--source`。来源须是 origin 为官方仓库的检出目录，或发行流程生成的包。Windows 的 `self update -e win` 提示使用 Scoop。`self status --json` 输出稳定字段，不自动同步版本。

## 源码安装

仍支持 `scripts/bootstrap.ps1` 同时部署两端、`scripts/install.ps1` 安装 Windows profile 入口、`sudo bash scripts/install.sh` 部署 WSL。bootstrap 与 Windows 到 WSL 的部署助手先验证 Git origin 再执行安装器。先 `git pull --ff-only`，再运行安装器或 `self update -e wsl` 更新部署。

## 发行维护

控制程序版本必须与 `pyproject.toml` 和 tag 一致。发布前完成两平台测试和 Ruff，然后构建候选包：

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m dev_tools.release --tag v0.4.1 --output dist
```

生成 `dev-tools-<版本>.zip`、`SHA256SUMS` 和 `dev-tools.json`。ZIP 含两平台程序、文档、示例和逐文件 SHA256 清单，固定排序、时间和换行，使用无压缩存储以避免两平台压缩库差异，排除缓存与 `*.local.*`。Scoop 清单使用 ZIP 的真实 SHA256。

可以先为已提交的源码创建 GitHub Release 草稿，上传三个附件和 `docs/releases/v<版本>.md` 中的说明；草稿不会触发发布工作流。

推送匹配版本的 `v<版本>` tag，或正式发布 GitHub Release 后，`.github/workflows/release.yml` 验证两平台并构建产物。已有草稿会更新附件并发布；已有正式 Release 只核对附件是否与构建一致，不覆盖正式附件。随后更新默认分支的 `bucket/dev-tools.json`。手动从普通分支运行工作流只验证和构建，不发布。工作流需有仓库写权限；受保护分支若禁止直接写入，应将 Release 清单通过 PR 合入。

首次 Release 和 bucket 清单发布是独立的维护动作。仅有本地代码和 `dist` 候选包时，公共 `scoop install dev-tools/dev-tools` 尚不可用。
