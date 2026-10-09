# 配置与运行说明

[English](usage.en.md) · [返回 README](../README.md)

## 三类配置与本机绑定

| 文件 | 职责 | 保存位置 |
|---|---|---|
| `config.toml` | dev-tools 的语言、编辑器、信息展示、报告和 WSL 发行版偏好 | 各平台用户配置目录 |
| `dev-tools.toml` | 任务、服务、依赖图、构建和同步策略 | 项目根目录，可提交 |
| `mise.toml` / `.mise.toml` | 项目运行时版本 | 项目根目录，可提交 |
| 注册 JSON | 源码、原生工作目录、运行用户和存储身份 | 各平台本机注册目录 |

项目准备只使用目标项目根 mise 文件中的显式版本。已有根配置不会被覆盖；同时存在两种根文件时合并工具声明，同优先级版本冲突会拒绝执行。用户偏好、用户全局 mise 默认值以及父级、嵌套版本声明不能替代项目根版本。
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
collectors = ["git", "mise", "winget", "store", "scoop", "apt", "snap", "rustup", "npm", "wsl"]
```

完整注释模板见 [settings.example.toml](../config/settings.example.toml)。

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
| mise | 读取当前平台已安装版本；刷新时用 outdated 查询当前声明允许的更新，遵守版本固定、缓存及发布冷却策略；启用 wsl 时也查询目标发行版 |
| winget | Windows 查询可更新软件；关闭刷新时跳过，避免软件源自行刷新 |
| store | Windows 用微软 Store CLI 查询全部已安装商店应用的更新，不使用 --apply，仅提供否定确认；CLI 拒绝非交互输入时保留结果并标记 partial；缺少 CLI 时记录 missing；未提供目标版本时记为未知 |
| scoop | Windows 查看软件状态；刷新时更新 Scoop/桶索引，不更新已安装应用 |
| apt | WSL 查看可升级包；刷新时更新 APT 索引，非 root 使用非交互 sudo，权限不足会记录失败 |
| snap | WSL 读取已安装包，刷新时仅用 refresh --list 查询版本和 revision 更新，不执行安装 |
| rustup | Windows/WSL 查询工具链和 rustup 自身更新；支持原生用户 Cargo 目录，不读取 shell profile，与 mise 安装清单分别记录 |
| npm | 读取全局包并查询额外包的更新；排除 mise 管理的包、npm 和 corepack |
| wsl | 允许 Windows 在配置的发行版中执行已启用的 mise/APT/Snap/Rustup/npm 采集器 |

report 默认刷新，可能联网或更新本机索引；不会安装或升级软件，也不接受自定义脚本采集器。
`--no-refresh` 禁止 Git fetch、索引刷新及 mise/Store/Snap/Rustup 更新查询，但 npm 更新查询仍可能联网；无需该查询时从 collectors 移除 npm。无法确定 mise 管理的 npm 清单时跳过 npm 更新查询。WSL 跳过 Windows 采集器。
mise、Store、Snap、Rustup 更新查询输出结构化 updates；Rustup 的更新退出码 100 不作为失败，部分有效结果会保留。未知响应格式标记 parse-error，不据此报告全部最新。

## 生命周期与平台边界

`dev-tools list` 只读查询当前平台；`list -e win` / `list -e wsl` 查询单侧，`list --all` 汇总 Windows 和配置的 WSL 发行版，同名注册按环境保留两行。输出由调用端按本机语言统一渲染；查询不会更新部署或改变项目运行状态。
`list --all --json` 的 environment 为 all，projects 中保留每个项目的环境，并返回 complete 和 errors。一侧查询失败时保留另一侧项目、complete=false、退出码为 1；两侧成功时 complete=true、退出码为 0。远端查询超时为 30 秒。

同一 Python worker 在两侧处理服务依赖、健康检查、重启、监控和恢复。依赖先启动并等待就绪，停止按相反顺序执行；启动失败回滚本次新增的进程。原生进程未声明健康检查时状态为 unknown；Compose 还会检查容器运行状态和容器报告的健康状态。

start 自动校验并执行统一准备计划：准备平台依赖与根级运行时，同步源码、锁文件和包，准备 Compose 镜像与 Spring classpath，再启动服务。运行期间声明、锁文件和配置变化会自动执行相同流程；后台保留监控进程，失败保存进度，修正后重试。默认启动全部服务时会跟进新发现的服务；start --service 只维护所选服务及依赖。显式 prepare 仍可预览或手动准备；stop 先取消运行意图，防止更新结束后重新启动。

source 构建保持服务运行；resource/structural 构建先停止受影响服务及依赖方，再构建和恢复。分支/HEAD 变化等待 Git 操作和文件稳定后，按声明刷新包与构建；后台准备可以补齐项目根明确声明的运行时和 Spring DevTools JAR。手动操作最多等待 15 秒获取项目锁，后台操作遇到忙碌项目时避让。

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
