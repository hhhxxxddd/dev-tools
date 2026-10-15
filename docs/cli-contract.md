# CLI 与自动化接口约定

[English](cli-contract.en.md) · [配置与运行](usage.md) · [项目声明](project-config.md)

本约定从 0.5.0 起适用。公共接口是命令、退出码和 JSON 输出；Python 模块、worker 入口及本机状态文件不是供第三方调用的 API。

## 命令与输出

环境参数为 `-e win|wsl`，所有命令省略时使用当前平台，包括 list、config、sysinfo、report 和 self。显式环境在同端和跨端均生效，支持放在命令前后。跨端读取目标侧配置，不传递调用端 --config；Windows hosts 的实际操作仍只支持 win，指定 wsl 明确报错。项目名可以省略，但当前目录必须唯一匹配注册的源码或工作目录；多重匹配不会猜测。`list --all` 不能与显式环境参数同时使用。

`dev-tools ssh` 与 `dev-tools hosts` 不带子命令时显示对应帮助，等同于加 `-h` 或 `--help`，成功退出 0；显式 `-e` 查询目标端帮助。

`--json` 只选择输出格式。`scan` 只读扫描；`check` 只读检查完整准备计划；`init` 创建缺少的配置；`prepare`、`start` 执行准备。没有公开的 `--dry-run`。

支持 JSON 的命令在 stdout 输出一个 UTF-8 JSON 对象，过程日志与第三方输出使用 stderr。分别捕获两条流，不要解析 `2>&1` 的合并输出。跨平台调用也验证目标返回单个 JSON 对象；长时间准备不使用列表查询的 30 秒超时。

`logs` 输出原始日志，没有 JSON 模式；`--` 后的参数不作为控制器选项解释。`config edit --json`、`self update --json`、`self upgrade --json` 和 `ssh edit/connect --json` 是参数错误。语言仅由本机配置选择，默认中文；键名、错误代码、状态枚举和数字不随语言变化。说明、描述、诊断文字和第三方输出不适合机器匹配。

## 退出码

| 退出码 | 含义 |
|---|---|
| `0` | 查询或执行完成；不表示所有服务已就绪，也不表示没有可升级软件 |
| `1` | 运行失败、配置无效、扫描/初始化/准备检查被阻塞，或查询仅得到部分结果 |
| `2` | 用法或参数错误，包括未知命令、非法环境、无效参数组合 |
| `130` | 用户中断 |

`scan`、`init`、`check` 有未解决项时返回 `1`。`status` 查询成功返回 `0`，即使 `ready=false`。`list --all` 的环境查询失败时保留其他结果并返回 `1`；单个项目状态中的 `error` 不表示注册列表查询失败。查询到缺失状态（例如 `self status` 的 WSL 未部署）也可成功返回 `0`。

`ssh connect` 是原生客户端入口，保留客户端或会话的退出码（例如 7 或连接失败的 255），Ctrl+C 映射为 130；其原始输出不包装为 JSON。

`report` 保留完整报告。采集器明确记录 `failed`、`timeout`、`partial`、`parse-error`，存在 `discovery_issues`，或仓库明确记录 fetch 失败、`worktree=error`、`tracking=tracking-error` 时返回 `1`。可选工具 `missing`、禁用项 `skipped`、正常发现更新 `updates` 本身不使退出码非零。

## 格式版本

| 接口 | 当前版本 | 说明 |
|---|---|---|
| 项目 JSON 与统一错误文档 | `schema_version=3` | 公共 CLI 格式版本，与程序发行版本分别演进 |
| 本机注册 JSON | `schema_version=3` | 绑定存储格式版本；不因 CLI 版本变化自动升级或失效 |
| `dev-tools.toml` | `schema=1` | 项目声明格式，必须为整数 `1` |
| `self status` 成功 JSON | `schema_version=1` | 所选目标的部署状态，独立于项目 JSON 协议 |
| 本机 `config.toml` | 无版本字段 | 严格校验已知字段、类型和范围 |

程序版本由 `dev-tools --version` 输出。内部 worker、准备指纹、恢复进度与缓存记录不保证供外部编辑或长期解析。本轮未改变注册存储数值，也不迁移注册。

成功的 `config`、`sysinfo`、`report` 保留各自既有 JSON 形状，不强加项目协议的 `schema_version`。`self status` 改为独立的 `schema_version=1`，不再使用旧的 controller_version/wsl 结构。根据调用的命令解析字段。未知字段可忽略；不要依赖键顺序、缩进、未承诺的数组顺序或人类文案。新增字段允许扩展；删除字段、改变类型或既有语义须明确更新格式版本及本约定。

## 主要结果字段

项目 JSON 的 `environment` 使用 `windows|wsl`；`list --all` 顶层为 `all`，每个项目保留自己的环境。CLI 的 `win` 不改变 JSON 中既有的 `windows` 标识。本机路径为对应平台的原生路径。

| 命令 | 可依赖的主要字段和语义 |
|---|---|
| `scan` | `action=scan`；`tools` 为工具名到版本证据的对象；`conflicts/unresolved/diagnostics` 为数组 |
| `init` | `action` 保留 mise 配置处理结果，如 `created/preserved/unresolved`；`project_config.action/path` 单独描述项目配置，不把顶层 action 改成 `init` |
| `check` | `action=check`；布尔 `ready` 表示计划没有未解决项；`unresolved/tasks/resolution_tasks` 为数组；不表示已经安装或启动 |
| `prepare/start/stop/restart/build/unregister/rename` | 成功包含对应 `action`、`name`、`completed=true`；rename 的 name 为新注册名 |
| `register/show` | 包含 `binding`；show 另含共享声明 `project` |
| `status` | 布尔 `prepared/ready`；布尔或 null 的 `healthy`；对象 `services/workers`；诊断值或 null 的 `recovery/maintenance_error` |
| `list` | 数组 `projects` 包括状态或单项目错误记录；`--all` 另有布尔 `complete` 和数组 `errors` |

`status.ready` 包含准备指纹有效、服务就绪、监控运行和无待恢复操作等条件。`healthy=null` 表示至少一个服务健康状态未知；否则当前聚合值与项目就绪状态一致。单项探测应读取 `services.<name>.health`，值为 `healthy|unhealthy|unknown`。服务 `phase` 为 `starting|running|restarting|stopped|failed`；`pid/exit_code` 可以为 null。恢复与错误详情用于诊断，不应依赖其人类模板文本。

辅助命令主要字段：config 有 `path/state/settings/origins/errors`，state 为 `loaded|missing|invalid`；sysinfo 有 `system/config_state/sections/directories/tools/commands`；report 有 `collected_at/platform/repositories/discovery_issues/collectors`；self status 有 `schema_version=1/environment/controller_version/deployment`，environment 为 `windows|wsl`，deployment 有 `state/version`，已安装时包含目标原生 `path`，WSL 另有 `distro`。部署 state 为 `installed|missing|unavailable`，不可用或缺失时 controller_version 为 null；该状态查询仍返回 0。

## 工具箱结果

`ssh list [关键词] --json` 使用 `schema_version=3`、`action=ssh`、`operation=list` 和 `environment=windows|wsl`，省略环境时仅查当前端。结果含 `hosts/files/warnings` 数组和布尔 `complete`。主机字段为 `name/hostname/user/port/proxy_jump/proxy_command/source/line/unresolved_fields/conditional`；端口为整数或 null，`proxy_command` 为布尔或 null，`conditional` 为布尔。无法静态确定的字段为 null，并在 `unresolved_fields` 中列出（`hostname|user|port|proxy`）。`source/line` 指向别名声明；`warnings` 项有稳定的 `code/source/line`。完整读取返回 0，部分读取保留结果并返回 1；条件配置本身不视为读取失败。

`ssh show <别名> --json` 使用相同的 schema_version/action/environment，operation 为 show，以单个 host 对象替代 hosts 数组，另保留 files/warnings/complete；host 字段与 list 相同。show 仅做静态读取，不执行 ssh -G 或 Match exec。edit 使用声明原文件和目标侧编辑器，connect 调用目标原生 SSH；两者均不支持 JSON，缺少相应目标命令会在操作前明确报错。

`hosts` 的 JSON 使用 `schema_version=3`、`action=hosts`、`operation=list|profiles|backups|use|off|restore`、`environment=windows` 和原生 `path`。`list` 提供 `entries/profile/invalid_lines/conflicts/complete`；每个条目有 `address/names/enabled/comment/line/profile`，冲突有 `domain/version/addresses/lines`。`profiles` 提供 `profiles` 数组，每项有 `name/path/entries/status`，状态为 `active|inactive|modified|invalid`。`backups` 提供备份记录数组（`id/before/after/operation/can_restore`），校验值为 SHA-256。写入操作提供布尔 `changed`、字符串或 null 的 `backup`、布尔或 null 的 `dns_flushed`。`hosts edit --json` 为参数错误。所有查询只读；写入校验失败退出 1，冲突或不完整的条目仍可在清单结果中检查。

## 错误文档

明确请求 JSON 的运行错误、参数错误以及解析前的配置错误输出以下形状；无法识别命令时 `action` 为 null，否则为顶层命令名（例如 `self`）。`message` 是本地化的安全说明，不序列化完整命令参数、原始异常或异常中的 stdout/stderr。

```json
{
  "schema_version": 3,
  "action": "status",
  "error": {
    "code": "project_not_registered",
    "message": "项目未注册；请先执行 dev-tools register",
    "details": {}
  }
}
```

| error.code | 含义 | 可选 details |
|---|---|---|
| `invalid_arguments` | 命令用法或参数无效 | 无 |
| `invalid_config` | 本机或已识别的 TOML 配置无效 | 无 |
| `project_not_registered` | 指定项目未注册，或当前目录没有匹配注册 | 无 |
| `timeout` | 子进程、只读查询或服务就绪等待超时 | 数字 `timeout_seconds`（已知时） |
| `preparation_failed` | prepare/start 项目操作失败 | 整数 `returncode`（已知时） |
| `operation_failed` | 其他运行错误或目标环境未返回有效 JSON | 整数 `returncode`（已知时） |
| `target_unavailable` | 目标控制器缺失或连接检查失败 | `environment/controller_version/target_version/returncode`（已知时） |
| `target_version_mismatch` | 目标执行约定不兼容 | `environment/controller_version/target_version/returncode`（已知时） |
| `target_command_unsupported` | 目标版本不支持命令或参数 | `environment/controller_version/target_version/returncode`（已知时） |
| `target_response_invalid` | 目标 JSON 或协议不符合约定 | `environment/controller_version/target_version/returncode`（已知时） |
| `interrupted` | 用户中断 | 无 |

`details` 始终为对象，没有可公开的安全事实时为 `{}`。脚本应按 code 分支，不解析 message。原始第三方过程日志仍可能出现在 stderr，应作为日志处理。

Windows 跨端探测失败时，`target_unavailable` 的 details 可增加 `reason`：`powershell_missing`（未找到 PowerShell 7）、`interop_unavailable`（WSL 无法启动 Windows 程序）、`probe_timeout`（版本探测超时）、`entrypoint_invalid`（入口解析失败）或 `entrypoint_failed`（入口探测失败，包含 returncode）。人类输出保留具体原因，不把 root/sudo 的 PATH 缺失统一误报成部署缺失。

扫描/初始化/检查阻塞及列表/报告部分结果保留各自诊断 payload，不强行改成错误文档。无效配置的 `config --json` / `config check --json` 保留配置字段并增加 `schema_version/action/error`。非零退出码不意味着只有 error；按命令检查错误字段、`ready`、`complete` 或采集状态。

源码仓库中的 `tests/test_cli_contract.py` 集中验证接口约定，执行行为与只读边界另有功能测试覆盖。

`list` 默认只查当前环境；显式 `-e` 只查目标端，只有 `list --all` 汇总两侧。列表项目新增整数数组 `ports` 与字符串数组 `types`，来自显式健康检查、根 mise 和任务/服务工具声明；未知时为空数组，人类表格显示 `-`。

`start --all` 只操作当前或指定环境，不接受项目名或 `--service`。JSON 包含 `projects` 和布尔 `complete`，每项有 `name/status`（`started|skipped|failed`）；单项失败继续处理其余项目，整体退出 `1`。已有活动 worker 的项目跳过，不重新准备或启动。
