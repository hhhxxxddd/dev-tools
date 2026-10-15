# 配置与运行说明

[English](usage.en.md) · [返回 README](../README.md)

## 环境选择

所有命令省略 `-e` 时操作当前平台；`-e win` / `-e wsl` 显式选择目标，两端写法一致。`-e` 可以放在命令前后，`--` 后的内容仍作为普通参数。

```text
dev-tools list
dev-tools list --all
dev-tools list -e win
dev-tools config edit -e wsl
dev-tools sysinfo -e win --json
dev-tools report -e wsl --no-refresh --output report.json
dev-tools self status -e win --json
dev-tools upgrade -e win
```

配置、系统信息、报告和工具维护也支持双向操作。目标侧读取自己的配置与编辑器，调用端的 `--config` 不传给另一侧。跨端路径参数（项目目录、`--source`、报告 `--output`）按调用端解析并转换；已经是目标平台的绝对路径直接使用，报告文件可以尚不存在。Windows 到 WSL 的首次部署仍用 `self update -e wsl`。

原生 WSL 的注册、启停、部署和恢复需要 `sudo`；Windows 发起 WSL 控制时自动选择 root。WSL 发起 Windows 操作沿用 Windows 用户权限，Linux root 不等于 Windows 管理员。`hosts` 目前仅支持 Windows：从 WSL 使用 `hosts … -e win`，选择 WSL 会明确提示不支持，不会改变操作目标。没有目标端部署时，普通命令报告连接或部署错误，查询不会自动安装。

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
| store | Windows 用微软 Store CLI 批量发现候选，再按应用名称逐项查询；只有明确确认有更新的应用才列入 updates，已是最新的应用排除，无法确认的记录为 unverified；共用单项采集超时，不使用 --apply，仅提供否定确认；缺少 CLI 时记录 missing，目标版本缺失时记为未知 |
| scoop | Windows 查看软件状态；刷新时更新 Scoop/桶索引，不更新已安装应用 |
| apt | WSL 查看可升级包；刷新时更新 APT 索引，非 root 使用非交互 sudo，权限不足会记录失败 |
| snap | WSL 读取已安装包，刷新时仅用 refresh --list 查询版本和 revision 更新，不执行安装 |
| rustup | Windows/WSL 查询工具链和 rustup 自身更新；支持原生用户 Cargo 目录，不读取 shell profile，与 mise 安装清单分别记录 |
| npm | 读取全局包并查询额外包的更新；排除 mise 管理的包、npm 和 corepack |
| wsl | 允许 Windows 在配置的发行版中执行已启用的 mise/APT/Snap/Rustup/npm 采集器 |

report 默认刷新，可能联网或更新本机索引；不会安装或升级软件，也不接受自定义脚本采集器。
明确采集失败、超时、部分结果、解析错误或仓库发现问题时保留报告并返回 1；可选工具缺失、禁用项或正常发现更新本身不算执行失败。
`--no-refresh` 禁止 Git fetch、索引刷新及 mise/Store/Snap/Rustup 更新查询，但 npm 更新查询仍可能联网；无需该查询时从 collectors 移除 npm。无法确定 mise 管理的 npm 清单时跳过 npm 更新查询。WSL 跳过 Windows 采集器。
mise、Store、Snap、Rustup 更新查询输出结构化 updates；Rustup 的更新退出码 100 不作为失败，部分有效结果会保留。未知响应格式标记 parse-error，不据此报告全部最新。
Store 的 `discovery` 保留批量原始输出和候选，`checks` 保留逐项状态、原始输出及 `update_available` 判定（true/false/null）。批量确认输入失败、候选数量不完整或逐项失败/超时时标记 partial；未确认项不列入 updates，也不视为最新。文本报告只展示确认结果，避免重复显示批量误报。

## SSH

直接输入 `dev-tools ssh` 显示帮助，等同于 `dev-tools ssh -h`；指定 `-e win|wsl` 时显示目标端帮助。

```text
dev-tools ssh list
dev-tools ssh list my-server
dev-tools ssh list -e win
dev-tools ssh list -e wsl --json
dev-tools ssh show my-server --json
dev-tools ssh edit
dev-tools ssh edit my-server -e win
dev-tools ssh connect my-server -e wsl
```

省略 `-e` 操作当前平台。Windows 读取当前用户的 `~/.ssh/config` 和 `%PROGRAMDATA%/ssh/ssh_config`；WSL 读取调用用户的 `~/.ssh/config` 和 `/etc/ssh/ssh_config`，sudo 调用仍使用原用户的清单。查询不需要注册项目或管理员权限。

表格包含别名、地址、用户、端口、跳板机和配置读取状态。关键词按别名、地址或用户进行不区分大小写的子串过滤。只列出具体的 `Host` 别名，通配符规则用于补齐默认值，不从 `known_hosts` 推测服务器清单。

按用户配置、系统配置的顺序读取，首个生效值优先；支持全局和 Host 下的 `Include`、按文件名排序的通配文件、引号路径，以及 `~/`、`${HOME}`、已有环境变量、`%d` 和 `%u` 静态路径。相对 Include 以用户 `.ssh` 或系统 SSH 配置目录为基准。循环引用、超过 16 层嵌套、128 个文件、单文件超过 1 MiB、非 UTF-8 或无效配置保留可读取部分并返回 1。

清单是静态配置视图；复杂 `Match` 条件、依赖主机的 Include 路径无法静态求值时明确提示，受影响字段为“待确认”，不执行 `ssh -G`、`Match exec` 或代理命令，也不读取私钥、连接服务器或写文件。未显式声明时地址使用别名、用户使用当前调用用户、端口使用 22。`ProxyCommand` 只显示标记，不显示命令内容。JSON 提供来源文件和行号；无法读取完整配置时 `complete=false`。

`show <别名>` 精确匹配别名（不区分大小写），展示静态地址、用户、端口、代理标记及声明文件和行号；同名大小写冲突明确报错。它与 list 一样只读，不执行原生 SSH 或条件命令。

`edit [别名]` 使用目标侧配置的现有编辑器。省略别名时打开该用户的 `~/.ssh/config`，首次编辑才创建文件；WSL 新文件为 0600、新建目录为 0700，保留已有权限。指定别名时打开其实际声明文件，包括 Include 文件，保留原有注释、换行和规则，不进行整份配置重写。VS Code、Cursor、Vim、Nano 等支持跳行的编辑器会定位声明行，其他编辑器显示文件和行号供查找。别名不存在时先用不带别名的 edit 添加。

交互编辑后保存并关闭编辑器，回终端按 Enter 重新读取静态配置；识别到的问题以警告展示，返回 1 并保留编辑结果。这不是 OpenSSH 的完整语法或条件求值校验。用户配置通常无需提权；系统配置和只读 Include 文件仍需要现有写权限，命令不会自动提权。

`connect <别名或目标>` 直接调用目标侧的 `ssh.exe` / `ssh`，也可连接未出现在静态清单中的目标。终端输入输出、主机指纹确认、密码、密钥与 agent 均由原生客户端处理，按其原生配置求值 Match、代理和连接行为。WSL sudo 调用以原用户身份连接，保留当前可用的 SSH_AUTH_SOCK；不跨端复制密钥或 agent。缺少原生客户端时提示在目标环境安装，不自动安装。

`edit/connect` 不支持 `--json`。connect 保留原生客户端或会话退出码，包括连接失败的 255，Ctrl+C 为 130；其他命令继续使用统一 CLI 退出码。

跨端 list/show 结果由调用端渲染；edit/connect 在目标侧执行，连接前只读检查目标命令是否存在。目标端尚未加入相应 SSH 操作时提示更新该端，不自动部署或升级。

## Windows hosts

直接输入 `dev-tools hosts` 显示帮助，等同于 `dev-tools hosts -h`，在 WSL 中也可查看。

```text
dev-tools hosts list
dev-tools hosts list app.test --json
dev-tools hosts edit
dev-tools hosts edit dev
dev-tools hosts profiles
dev-tools hosts use dev
dev-tools hosts off
dev-tools hosts backups
dev-tools hosts restore
```

此模块仅管理 Windows 系统 hosts，通过系统 API 定位文件；不修改 WSL 的 `/etc/hosts`。在 WSL 使用上述命令时显式加 `-e win`，所有方案、备份、编辑器和权限仍在 Windows 端处理，调用端配置文件不跨端传递。目标端需要包含 hosts 命令。

`list [关键词]` 按 IP、域名或备注过滤，表格展示地址、域名、备注、所属方案与状态；注释中的合法映射标为已禁用，现有的同域名、同 IP 协议族地址冲突标黄。原有无效行保留并报告行号，读取不完整时退出 1；配置冲突本身不代表清单读取失败。查看、列方案和列备份不会创建文件、提权或刷新 DNS。

`edit` 编辑当前 hosts，`edit dev` 编辑名为 dev 的方案，不立即启用。使用配置中的编辑器，其次 VISUAL、EDITOR，最后记事本；先编辑 UTF-8 草稿，保存并关闭编辑器后在终端按 Enter 提交，Ctrl+C 取消。不使用公开的 `--json` 编辑模式；需要脚本化写入时使用已保存方案的 `use --json`。校验失败、编辑器失败或文件在编辑期间被改动时保留草稿，拒绝覆盖。

方案使用普通 hosts 文本，例如：

```text
# 本地开发
127.0.0.1 app.test api.test
::1 app.test
```

方案名为 1～64 个字母、数字、下划线或连字符，不能是 Windows 保留设备名。方案文件存放在 Windows 用户原生配置目录下的 `dev-tools/hosts/profiles/<名称>.hosts`（通常在 `%LOCALAPPDATA%`），不跟随 `--config` 改变位置，也不写入项目配置。方案必须使用有效 IP 和具体域名，可含注释和多个别名，不支持通配域名。IPv4、IPv6 可分别映射同一域名；同协议族的不同 IP 映射拒绝应用。

`use dev` 校验方案和它与手工条目的冲突，再替换 `# BEGIN dev-tools hosts: dev` 至 `# END dev-tools hosts` 托管区块，其余条目与注释保留。已有的无关冲突或无效行不会阻止切换；直接编辑允许保留旧问题，禁止引入新的无效行或冲突。`off` 只移除该区块。`profiles` 标明已启用、未启用、无效或启用后有改动；修改方案文件后需再次 `use` 生效。区块标记损坏或重复时拒绝切换，可用 `edit` 修复。

系统写入前展示差异并在 `dev-tools/hosts/backups` 保存原始字节及事务校验记录，无实际差异时不写文件、不备份、不提权。仅写入时按需弹出 UAC；原文件的编码、BOM、区块外换行和 Windows 权限得到保留，写入使用文件替换并刷新 DNS 缓存。单文件限制 10 MiB。安全软件拒绝时不修改安全设置；取消 UAC 或发生并发改动时拒绝写入。

`restore [备份编号]` 默认选择与当前文件校验值匹配的最近备份，恢复前再次备份。若该备份之后还有其他改动，拒绝整文件覆盖，请根据 `backups` 中的记录手动合并。备份不自动删除。除交互 `edit` 外均支持 `--json`，它只改变输出格式；`use/off/restore --json` 仍执行真实修改。

## 生命周期与平台边界

WSL 的 root 或 sudo 环境也可使用 `-e win` 查询、注册和控制 Windows 项目。即使 Linux PATH 不含 Windows 目录，也会根据实际磁盘挂载定位 PowerShell 7；必要时通过 Windows 自带 PowerShell 只读查询 Windows PATH 和安装记录，不读取用户 profile 或改动系统配置。WSL root 不会自动获得 Windows 管理员权限，Windows hosts 写入仍按需请求 UAC。命令为 `dev-tools hosts list -e win`（`hosts` 为复数）。

`dev-tools list` 默认只读查询当前环境；`list -e win` / `list -e wsl` 查询指定侧。只有 `list --all` 汇总 Windows 和配置的 WSL 发行版，同名注册按环境保留两行。输出由调用端按本机语言统一渲染，包含配置声明的端口与运行时类型；查询不会更新部署或改变项目运行状态。
表格列顺序为名称、环境、端口、类型、状态。交互终端中，运行中/正常为绿色，待恢复/启动中/部分运行为黄色，失败/异常为红色，已停止/未知为灰色；工具自身的错误为红色、操作提示为黄色。重定向输出或设置 `NO_COLOR` 时不添加颜色，JSON 保留原始状态标识和结构。
汇总列表中，某一端不可用时以黄色提示，保留另一端的结果；JSON 仍返回 `complete=false`、`errors` 和退出码 1。`list -e wsl` / `list -e win` 只查指定端。两端安装副本分别更新，查询不会安装或更新另一端；版本相同也可用 `self update -e wsl` 重新部署当前 Windows 源码。
`start`（含 `--all`）和 `restart` 在终端显示依赖分析、环境准备、同步、依赖/构建任务、服务启动、健康检查及监控启动的阶段与耗时；长步骤每 5 秒显示一次等待提示。进度写到 stderr，`--json` 使用原有 JSON 结果，后台维护不输出终端进度。任务原始输出仍可通过 `dev-tools logs my-app install --task` 查看，将 `install` 替换为实际任务名。
`list --all --json` 的 environment 为 all，projects 中保留每个项目的环境，并返回 complete 和 errors。一侧查询失败时保留另一侧项目、complete=false、退出码为 1；两侧成功时 complete=true、退出码为 0。远端查询超时为 30 秒。

同一 Python worker 在两侧处理服务依赖、健康检查、重启、监控和恢复。依赖先启动并等待就绪，停止按相反顺序执行；启动失败回滚本次新增的进程。原生进程未声明健康检查时状态为 unknown；Compose 还会检查容器运行状态和容器报告的健康状态。

start 自动校验并执行统一准备计划：准备平台依赖与根级运行时，同步源码、锁文件和包，准备 Compose 镜像与 Spring classpath，再启动服务。运行期间声明、锁文件和配置变化会自动执行相同流程；后台保留监控进程，失败保存进度，修正后重试。默认启动全部服务时会跟进新发现的服务；start --service 只维护所选服务及依赖。check 只读检查计划，prepare 手动执行准备；stop 先取消运行意图，防止更新结束后重新启动。

```text
dev-tools check my-app
dev-tools check my-app --json
dev-tools prepare my-app
dev-tools prepare my-app --json
```

`check` 显示声明版本、平台前置条件、准备任务和阻塞原因，不安装运行时、同步源码或执行任务。它与 `prepare`、`start` 及后台维护使用同一准备计划；`scan` 则只负责发现项目声明。`--json` 只控制输出格式，`prepare --json` 仍会执行准备。在唯一匹配项目的目录中可省略项目名。
阻塞的 scan/init/check 返回 1；参数错误返回 2，中断返回 130。查询成功的 status 返回 0，并通过 JSON 的 ready 表达就绪状态。完整约定见 [CLI 与自动化接口](cli-contract.md)。

source 构建保持服务运行；resource/structural 构建先停止受影响服务及依赖方，再构建和恢复。分支/HEAD 变化等待 Git 操作和文件稳定后，按声明刷新包与构建；后台准备可以补齐项目根明确声明的运行时和 Spring DevTools JAR。手动操作最多等待 15 秒获取项目锁，后台操作遇到忙碌项目时避让。

| 内容 | Windows | WSL |
|---|---|---|
| 注册 | `%LOCALAPPDATA%/dev-tools/projects.d` | `/etc/dev-tools/projects.d` |
| 状态和日志 | `%LOCALAPPDATA%/dev-tools/state` | `/var/lib/dev-tools` |
| 工作目录 | 原始源码目录 | 运行用户的 `~/.cache/dev-tools/build` 下独立目录 |
| 监督 | 隐藏进程，校验 PID 和启动时间 | `dev-tools-worker@.service` 通用模板 |
| 源码同步 | 直接使用源码 | rsync，排除 Git、依赖和平台构建产物 |

### 日志查询与保留

`dev-tools logs my-app api --lines 200` 查看服务最近的日志；添加 `--follow` 持续跟踪。查询只读取当前日志，初始尾部最多读取 10 MiB，因此超长行或大量输出时可能少于指定行数；持续跟踪按块读取后续内容，并跟进文件轮转或截断。

worker 和任务启动前，已有日志达到 10 MiB 时轮转，最多保留 `.1`～`.3` 三份历史文件；超大的历史日志只保留最近 10 MiB。轮转只在启动时发生，一次长时间运行的当前日志仍可能超过 10 MiB，不能将此阈值视为实时大小限制。

WSL prepare 可补齐 Git、rsync、运行用户工具及请求的 Docker/Compose；Windows 使用已配置且可达的原生 Docker 引擎。
Redis/MySQL 等业务服务由项目自己声明，不自动推断或安装。本仓库独立运行，不读取或迁移 wsl-devctl 的注册信息。
重命名保留缓存、状态、Maven/Compose 存储身份；配置损坏时仍可依据有效快照停止或取消注册。

## 工具箱诊断

以下命令均可在 Windows 和 WSL 原生运行，也可通过 `-e win|wsl` 双向调用。默认查询当前平台，使用目标端配置与原生数据，不要求注册项目，不安装依赖或申请提权。工具箱命令 `ssh`、`hosts`、`ports`、`which`、`net` 单独执行均显示帮助，等同于加 `-h`；具体操作的必需参数仍会校验。

### 端口占用

```text
dev-tools ports list
dev-tools ports list --protocol tcp -e wsl
dev-tools ports show 3000 -e win --json
```

查询实际 TCP 监听端口与已绑定的 UDP 端口，包含 IPv4/IPv6、地址、端口、PID、进程名称、可读取的可执行文件路径与项目归属。Windows 使用原生 API，WSL 读取 `/proc`，按端口与协议过滤后采集关联进程；不依据项目声明猜测实际监听情况。

项目归属在 Windows 通过活动 worker 的 PID、启动时间及当前进程树交叉匹配，在 WSL 使用当前 systemd 服务的 cgroup 身份，避免系统时间调整影响匹配；不凭端口或进程名猜测。Compose 转发及无法确认的进程保持空归属。权限不足或快照期间进程退出可能使 PID 为空，此时保留端口、显示警告并返回 1。没有匹配端口返回空清单和 0。本命令不结束进程。

### 命令路径

```text
dev-tools which python
dev-tools which node -e wsl --json
```

按目标端当前进程的 PATH 顺序列出候选，Windows 同时遵循 PATHEXT 的扩展名顺序。第一项标记为命中；重复路径合并，链接展示可静态解析的落点。来源根据原生 mise、Scoop、虚拟环境目录标识，其他候选保留为 PATH 来源。Windows 应用执行别名不能静态解析落点时为 null。

不执行命令、shim、shell profile 或项目代码，不求值 shell 别名、函数及动态 shim，也不将控制程序的 Python 绑定当作项目的运行时。此结果描述 PATH 候选，不保证与交互 shell 或项目 mise 执行环境完全一致。找到且完整返回 0，未找到或读取不完整返回 1。

### 网络连接

```text
dev-tools net info -e win
dev-tools net info -e wsl --json
dev-tools net check localhost:3000 -e win
dev-tools net check https://example.com/health -e wsl --timeout 5
dev-tools net check [::1]:3000 --json
```

`info` 查看目标端 DNS 服务器与环境代理，隐藏代理凭据。Windows 额外展示 `.wslconfig` 中选定的网络声明；WSL 在原生 `wslinfo` 可用时报告实际网络模式。声明与实际模式分别呈现，无法探测时保留未知，不推断默认值。代理信息只来自环境变量，不代表所有应用或 Windows 系统代理的最终生效配置。

`check` 使用直连：主机和端口测试 DNS/TCP，HTTP 地址继续发送 HEAD，HTTPS 地址先验证证书和 TLS。HTTP 400 及以上返回失败，不跟随重定向、不读取正文，不自动改用 GET；URL 查询参数发送到目标但不回显，地址中不允许携带用户凭据。显示的环境代理仅供排查，连接测试不使用它们。

`--timeout` 为每个阶段的上限，范围 1～60 秒，默认 5 秒；总耗时可能为多个阶段之和。DNS 在隔离的标准库子进程中执行，HTTP 阶段也限制总时长，失败后不执行后续阶段。成功返回 0，任一阶段失败返回 1，参数错误返回 2。不会修改 DNS、代理、hosts、防火墙或 WSL 配置。
