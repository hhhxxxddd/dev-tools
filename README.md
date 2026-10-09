# dev-tools

[English](README.en.md)

在 Windows 和 WSL 上，用同一套命令发现项目、准备开发环境并管理服务。
项目共享版本和工作流声明，各平台使用自己的运行时、依赖、缓存与进程监督。

当前版本 **0.4.0**；扫描与项目命令的 JSON schema 为 **3**，`dev-tools.toml` schema 为 **1**。

## 安装

### 同时安装 Windows 与 WSL 入口

需要 PowerShell 7、Git 和一个已经可用的 WSL 发行版。WSL 服务控制需要正常运行的 systemd。
Windows 已安装 mise 时直接使用；缺少 mise 时需要已有的 Scoop。
WSL 自动安装 mise 使用 Debian/Ubuntu 的 extrepo/APT 来源。

```powershell
git clone https://github.com/hhhxxxddd/dev-tools.git
cd dev-tools
.\scripts\bootstrap.ps1
. $PROFILE
dev-tools help
```

bootstrap 默认处理 Ubuntu；其他发行版使用 `-Distro <发行版名>`。它先验证检出目录的 Git origin，再安装缺失的 mise、部署宿主和入口。它不会安装 WSL 或 Scoop。安装完成后，运行时目标发行版仍由本机配置的 `[wsl].distro` 选择；使用其他发行版时请同步修改该设置。

### 分别安装

已有 mise 的 Windows：

```powershell
.\scripts\install.ps1
. $PROFILE
```

已有 mise、rsync 和 systemd 的 WSL：

```bash
sudo bash scripts/install.sh
dev-tools help
```

控制程序使用 Python **3.14.8** 宿主，独立于目标项目的 Python。Windows 入口指向当前检出目录；WSL 将程序复制到 `/opt/dev-tools`，宿主位于 `/opt/dev-tools/host-mise`，入口为 `/usr/local/bin/dev-tools`。安装器不修改用户全局 mise 工具声明，也不注册或启动项目。

更新时先在源码检出目录执行 `git pull --ff-only`，再运行对应安装器；也可以重新运行 bootstrap 更新两侧部署。
卸载使用 `scripts/uninstall.ps1` 或 `sudo bash scripts/uninstall.sh`。WSL 卸载会停止活动 worker 并移除入口和 systemd 模板；项目声明、注册、状态、缓存和运行时保留。

## 从一个项目开始

在项目源码目录执行：

```text
dev-tools scan
dev-tools init --dry-run
dev-tools init
dev-tools register
dev-tools prepare --dry-run --json
dev-tools prepare
dev-tools start
dev-tools status
dev-tools list
```

1. `scan` 解析已知元数据中的版本声明；`init` 生成缺少的 `dev-tools.toml` 和根级 mise 配置。两者只解析项目元数据，不运行项目代码、包脚本、Wrapper 或下载内容；init 的实际操作仅写入缺少的声明。
2. 检查生成的版本、启动参数、端口和健康检查。已有文件保留；无法确定的版本、同优先级冲突和损坏元数据会报告来源，需先处理诊断。
3. `register` 创建本机绑定。`prepare --dry-run` 使用与实际准备相同的计划进行校验；只有显式 prepare 安装项目运行时、准备依赖和框架产物。
4. `start` 启动已准备的服务和监控。声明、锁文件或相关构建元数据改变后，按诊断重新 prepare；启动不会补装运行时。

自动发现支持 Node 包管理工作区、Maven/Spring Boot、Python/uv 和已有 Compose 声明。扫描还可以读取 Gradle 等版本元数据，但不保证自动生成对应工作流。复杂项目可直接编辑声明或参考[项目配置文档](docs/project-config.md)与[示例](examples/README.md)。

`init --runtime auto|host|compose` 选择工作流类型，默认 auto 优先已有 Compose。`init --toolchain system` 使用已安装的系统工具；默认 mise 从项目根版本声明选择运行时。

### 选择 Windows 或 WSL

项目命令支持 `-e win|wsl`（也可用 `--env`），省略时使用当前平台。环境参数可以放在命令前或后：

```text
dev-tools -e wsl register .
dev-tools prepare my-app -e wsl --dry-run
dev-tools -e wsl prepare my-app
dev-tools -e wsl start my-app
dev-tools -e win list
```

跨平台调用转换路径并传递调用者的当前目录。CLI 使用 win，共享配置覆盖表和 JSON 中的平台标识使用 windows。

原生 WSL 的 register、prepare、start、stop、restart、sync、build、rename、unregister 需要 root，应使用 `sudo dev-tools ...`；prepare 的 dry-run 和只读操作无需 root。从 Windows 转发修改操作时自动以 WSL root 控制，注册默认使用发行版默认用户运行项目，也可通过 `register --user` 指定用户。Windows 原生操作使用当前用户。

scan/init/register 的目录默认当前目录。其他项目操作可以省略项目名，但当前目录必须唯一匹配所选平台已注册项目的源码目录或工作目录，包括其子目录；没有匹配或多重匹配时必须指定名称。

## 命令一览

项目命令直接位于顶层。`dev-tools` 或 `dev-tools help` 显示全部命令，`dev-tools help <命令>` 与 `dev-tools <命令> --help` 查看参数。

| 命令 | 用途与主要参数 |
|---|---|
| `scan [PATH]` | 静态识别工具版本与诊断；`--json` |
| `init [PATH]` | 生成缺少的声明；`--dry-run`、`--name`、`--runtime`、`--toolchain`、`--json` |
| `register [PATH]` | 创建本机绑定；`--name`、`--user`（WSL）、`--force`、`--json` |
| `list` | 列出所选平台的项目与状态；`--json` |
| `show [NAME]` | 查看声明与本机绑定；`--json` |
| `prepare [NAME]` | 安装并准备项目；`--dry-run`；`--json` 必须同时指定 `--dry-run` |
| `start [NAME]` | 启动服务和监控；`--service` 包含该服务的依赖；`--json` |
| `stop [NAME]` | 停止项目全部服务和监控；`--json` |
| `restart [NAME]` | 重启项目服务和监控；`--json` |
| `status [NAME]` | 查看准备指纹、服务、健康检查和恢复状态；`--json` |
| `logs [NAME] SERVICE` | 服务或 `__sync`/`__watch` 日志；`--task`、`--lines`、`--follow` |
| `sync [NAME]` | 手动同步源码到原生工作目录；`--json` |
| `build [NAME]` | 使用已准备的运行时构建；`--service`、`--kind branch\|source\|resource\|structural`、`--json` |
| `rename [NAME] NEW_NAME` | 修改本机别名并恢复原活动服务；`--json` |
| `unregister [NAME]` | 停止服务和监控并取消注册；`--purge`（仅 WSL）、`--json` |
| `config [edit\|check]` | 查看、编辑或校验本机配置；`--json` 用于查看和校验 |
| `sysinfo` | 只读查看当前机器、配置目录和 PATH 工具；`--json` |
| `report` | 收集 Git 与软件信息；`--refresh`/`--no-refresh`、`--timeout`、`--output`、`--json` |
| `help [COMMAND]` | 查看全部命令或指定命令的帮助 |

所有命令支持 `--config FILE`，程序版本使用 `dev-tools --version`。config、sysinfo、report 在当前平台运行，不接受项目专用的 -e 参数。

logs 始终需要服务或任务名称；rename 始终需要新名称。`register --force` 允许为同一源码和运行用户增加绑定，不覆盖冲突绑定。
unregister 默认保留源码、状态、缓存和运行时；WSL 的 `--purge` 只删除经边界验证的原生工作目录，Windows 不支持删除源码。

## 三类配置与本机绑定

| 文件 | 职责 | 保存位置 |
|---|---|---|
| `config.toml` | dev-tools 的语言、编辑器、信息展示、报告和 WSL 发行版偏好 | 各平台用户配置目录 |
| `dev-tools.toml` | 任务、服务、依赖图、构建和同步策略 | 项目根目录，可提交 |
| `mise.toml` / `.mise.toml` | 项目运行时版本 | 项目根目录，可提交 |
| 注册 JSON | 源码、原生工作目录、运行用户和存储身份 | 各平台本机注册目录 |

项目准备只使用目标项目根 mise 文件中的显式版本。已有根配置不会被覆盖；两种根文件的同优先级版本冲突会拒绝执行。用户偏好、用户全局 mise 默认值以及父级、嵌套版本声明不能替代项目根版本。
共享声明不包含本机源码路径、用户名或缓存根目录。Windows 和 WSL 可以读取同一份源码声明，但不共享可执行目录、虚拟环境、依赖和构建产物。

### dev-tools 本机偏好

```text
dev-tools config
dev-tools config edit
dev-tools config check
dev-tools config --json
```

默认文件：Windows 为 `%LOCALAPPDATA%/dev-tools/config.toml`；WSL 为 `$XDG_CONFIG_HOME/dev-tools/config.toml`，未设置 XDG 时为 `~/.config/dev-tools/config.toml`。通过 sudo 调用时，WSL 使用原调用用户的配置，并以该用户运行编辑器。

文件选择优先级为 `--config FILE` > `DEV_TOOLS_CONFIG` > 平台默认路径。`--config` 可以放在命令前后。缺少文件时直接使用默认值，只有 config edit 创建模板；无效配置会报错，帮助和 config 的查看、编辑、校验仍可使用。未知字段会被拒绝，配置中的相对目录以配置文件所在目录为基准。

```toml
language = "zh"
editor = []

[wsl]
distro = "Ubuntu"

[sysinfo]
sections = ["system", "directories", "tools"]
show_missing = true
# 省略 tools 使用平台默认清单；显式列表整份替换，[] 禁用。
# tools = [{ command = "git", description = { zh = "版本控制", en = "Version control" } }]
directories = []

[report]
roots = []
max_depth = 2
timeout = 90
refresh = true
collectors = ["git", "mise", "winget", "scoop", "apt", "npm", "wsl"]
```

完整注释模板见 [settings.example.toml](config/settings.example.toml)。

- **语言**：zh 为默认中文，en 为英文；下次调用生效，没有语言命令行参数或公开语言环境变量。帮助、操作提示和工具自身诊断切换语言，第三方原始输出、用户文本、JSON 字段与状态标识保留原值。跨环境调用传递调用者选定的语言，两侧配置文件仍各自独立。
- **编辑器**：非空 editor argv 数组 > VISUAL > EDITOR > Windows 记事本 / WSL vi。例如 `editor = ["code", "--wait"]`；编辑器必须已安装。编辑器退出后校验配置；GUI 启动器可能提前返回，保存后可再次执行 config check。
- **发行版**：`[wsl].distro` 同时用于跨平台控制和 Windows 报告。`DEV_TOOLS_DISTRO` 可临时覆盖；安装器生成的 PowerShell 函数不固定发行版。
- **sysinfo**：sections 控制展示部分，tools 控制 PATH 探测、说明和顺序，directories 配置本机目录与可选入口名。说明可为字符串或 `{ zh = "...", en = "..." }`。`show_missing = false` 隐藏缺失项；关闭的部分不执行探测。工具不会被执行，也不读取 PowerShell profile。
- **report**：roots 是 Git 仓库扫描范围，默认 [] 不扫描仓库；max_depth 为 0～5，单项超时为 1～600 秒，默认 90 秒。显式 `collectors = []` 不执行采集器。CLI 的超时和刷新参数只覆盖本次运行。

### 报告的采集与刷新

```text
dev-tools sysinfo --json
dev-tools report --no-refresh
dev-tools report --json --output report.json
```

| 采集器 | 行为 |
|---|---|
| git | 在 roots 内发现仓库，查看工作区、分支和远端差异；刷新时 fetch 引用，不 pull |
| mise | 读取当前平台已安装的工具版本；启用 wsl 时也读取目标发行版 |
| winget | Windows 查询可更新软件；关闭刷新时跳过，避免软件源自行刷新 |
| scoop | Windows 查看软件状态；刷新时更新 Scoop/桶索引，不更新已安装应用 |
| apt | WSL 查看可升级包；刷新时更新 APT 索引，非 root 使用非交互 sudo，权限不足会记录失败 |
| npm | 读取全局包并查询额外包的更新；排除 mise 管理的包、npm 和 corepack |
| wsl | 允许 Windows 在配置的发行版中执行已启用的 mise/APT/npm 采集器 |

report 默认刷新，可能联网或更新本机索引；不会安装或升级软件，也不接受自定义脚本采集器。
`--no-refresh` 禁止 Git fetch 和索引刷新，但 npm 更新查询仍可能联网；无需该查询时从 collectors 移除 npm。无法确定 mise 管理的 npm 清单时跳过 npm 更新查询。WSL 跳过 Windows 采集器。

## 生命周期与平台边界

同一 Python worker 在两侧处理服务依赖、健康检查、重启、监控和恢复。依赖先启动并等待就绪，停止按相反顺序执行；启动失败回滚本次新增的进程。原生进程未声明健康检查时状态为 unknown；Compose 还会检查容器运行状态和容器报告的健康状态。

prepare 校验计划后停止原活动 worker，准备平台依赖和根级运行时，同步源码、执行任务、准备 Compose 镜像和 Spring classpath，再恢复原活动集合。失败保存进度与恢复记录；修复后重新 prepare 恢复。显式 stop 会取消待恢复的运行意图。

source 构建保持服务运行；resource/structural 构建先停止受影响服务及依赖方，再构建和恢复。分支/HEAD 变化等待 Git 操作和文件稳定后，按声明刷新包与构建；后台 worker 不安装运行时或下载 Spring DevTools JAR。手动操作最多等待 15 秒获取项目锁，后台操作遇到忙碌项目时避让。

| 内容 | Windows | WSL |
|---|---|---|
| 注册 | `%LOCALAPPDATA%/dev-tools/projects.d` | `/etc/dev-tools/projects.d` |
| 状态和日志 | `%LOCALAPPDATA%/dev-tools/state` | `/var/lib/dev-tools` |
| 工作目录 | 原始源码目录 | 运行用户的 `~/.cache/dev-tools/build` 下独立目录 |
| 监督 | 隐藏进程，校验 PID 和启动时间 | `dev-tools-worker@.service` 通用模板 |
| 源码同步 | 直接使用源码 | rsync，排除 Git、依赖和平台构建产物 |

WSL prepare 可补齐 Git、rsync、运行用户工具及请求的 Docker/Compose；Windows 使用已配置且可达的原生 Docker 引擎。
Redis/MySQL 等业务服务由项目自己声明，不自动推断或安装。本仓库独立运行，不读取或迁移 wsl-devctl 的注册信息。
重命名保留缓存、状态、Maven/Compose 存储身份；配置损坏时仍可依据有效快照停止或取消注册。

## 代码结构与验证

```text
src/dev_tools/
  cli.py、command_help.py                 命令定义与本地化帮助
  settings.py、i18n.py、locales/           本机偏好与中英文输出
  scanner.py、metadata.py、versions.py     静态元数据与版本识别
  project_models.py、workflows.py          扫描模型、初始化与 mise 隔离
  projects/                               声明、计划、执行、驱动、生命周期和 worker
  runtimes/router.py                      跨环境命令与路径传输
  runtimes/platforms/                     原生存储、用户、同步、进程与 systemd
scripts/                                  安装与平台入口、底层进程适配
systemd/                                  通用 worker 模板
```

Python 无第三方运行依赖，支持 `>=3.14.8,<3.15`。开发验证使用本平台 Python，并将 src 加入 PYTHONPATH。

Windows：

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m unittest discover -v tests
uvx ruff check src tests
uvx ruff format --check src tests
```

WSL：

```bash
export PYTHONPATH="$PWD/src"
python3 -m unittest discover -v tests
uvx ruff check src tests
uvx ruff format --check src tests
```

入口改动还应验证 `scripts/dev-tools.ps1` 与 `scripts/dev-tools`；bootstrap 改动先解析所有 PowerShell 安装脚本。维护约束见 [AGENTS.md](AGENTS.md)。

真实验证脚本以临时注册、状态和缓存运行：`tests/integration/lifecycle_smoke.py` 检查 HTTP 服务、热构建、恢复和重命名；`toolchain_smoke.py` 使用已安装的 Java/Node/Python/Maven；`transport_smoke.py` 从 Windows 检查双向路径、隐式名称、帮助、配置和语言传递。WSL 生命周期测试需要 root 和 systemd，工具链测试不安装新版本；双向测试需要两侧入口已安装。

[MIT License](LICENSE)
