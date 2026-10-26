# 项目配置参考

[English](project-config.en.md) · [返回 README](../README.md)

`dev-tools.toml` 是项目根目录中的共享声明，描述如何准备和运行项目。
运行时版本写在同一根目录的 `mise.toml` 或 `.mise.toml`，本机偏好通过 `dev-tools config` 管理。
本机源码路径、运行用户、缓存和存储身份由 register 保存，不写进共享声明。

## 最小示例

适用于具有 package-lock.json 和 dev 脚本的 npm 项目；Node 版本需要在根级 mise 文件中声明。

```toml
schema = 1
name = "my-app"
toolchain = "mise"
rebuild_on_branch = true

[tasks.install]
command = ["npm", "ci"]
manager = "npm"
tools = ["node"]

[services.web]
command = ["npm", "run", "dev", "--", "--port", "5173"]
prepare = ["install"]
tools = ["node"]
restart = "on-failure"

[services.web.health]
tcp = 5173
timeout = 30
```

在 Windows 执行 `dev-tools register`、`dev-tools check --json`，检查后执行 start，自动准备环境。
原生 WSL 修改操作使用 sudo；预览无需 sudo。

## 根字段

| 字段 | 默认值或要求 | 含义 |
|---|---|---|
| schema | 必须为整数 1 | 声明格式版本 |
| name | 必填 | 项目默认注册名 |
| toolchain | mise | mise 使用根级显式版本；system 使用已有系统工具 |
| dependency_mode | auto | 自动同步已知包管理器锁文件；locked 禁止自动改锁文件 |
| discovery | manual；init 生成 auto | 持续发现模块并保留手改配置，或只使用手写声明 |
| scan | 空表 | 项目元数据发现范围；见下文 |
| rebuild_on_branch | true | 分支或 HEAD 变化稳定后触发准备任务和结构构建 |
| sync_exclude | [] | WSL 同步排除模式，用于保留自定义原生输出 |
| sync_include | [] | 优先于排除规则的 WSL 同步包含模式，只用于需要同步的源码 |
| tasks | 空表 | 命名任务 |
| services | 空表 | 命名服务 |
| builds | 空表 | 按服务命名的构建监控策略 |

项目、任务和服务名称为 1～63 个字符，以小写字母或数字开头，其余可以为小写字母、数字、点、下划线或连字符；Windows 保留设备名不允许使用。
未知字段、不存在的依赖和循环依赖会被拒绝。

WSL 默认排除 build、dist、target 和依赖目录。如果项目将 build 用作源码目录，可以显式设置
`sync_include = ["/frontend/build/***"]`；前导 / 表示相对项目根目录，*** 包含目录及其全部内容。
不要包含 Windows 安装目录、虚拟环境或依赖缓存。

工作目录、Compose 文件、Python 根目录和 classpath 模块都使用项目内相对路径，不接受绝对路径或 ..。
执行时还会检查解析后的路径是否越出项目。

## 扫描范围与版本校验

Windows、WSL 使用同一套静态扫描规则，不读取 `.gitignore`，不执行项目脚本、Wrapper 或下载内容。被 Git 忽略的本地源码仍可参与运行。

根配置始终纳入；手写工作流扫描任务和服务的 workdir、Python 根目录及到项目根之间的必要元数据。声明的 Node workspaces、pnpm packages、uv workspace members 和 Maven modules 直接展开，不受兜底深度限制。未声明的旁支目录不影响手写工作流。没有项目声明或 discovery=auto 时才进行兜底递归；嵌套 Git 仓库和拥有 dev-tools.toml 的独立项目默认作为边界，显式声明的目录可以纳入。

```toml
[scan]
roots = ["frontend", "backend", "tools/local-api"]
exclude = ["**/fixtures/**", "tools/review-copies/**"]
max_depth = 4
```

| 字段 | 默认值 | 含义 |
|---|---|---|
| roots | 自动推导 | 指定兜底发现起点；配置后替代默认搜索起点。显式 [] 关闭兜底发现，不排除已声明的任务或工作区 |
| exclude | [] | 补充元数据扫描排除规则；与已声明目录、必要元数据或工作区成员冲突时报告错误 |
| max_depth | 4 | 每个兜底起点算第 0 层；可设为 0～32，触顶时给出警告及被截断的目录 |

路径相对项目根，统一使用 /，也接受并规范化 Windows 反斜杠。roots 为具体目录；exclude 使用区分大小写的路径 glob，支持 *、?、**，末尾 / 表示目录。不支持绝对路径、..、否定规则或读取 .gitignore。默认跳过依赖、构建、缓存和 .local 等目录；显式 workdir、workspace 成员或 roots 可以纳入这些目录，但始终拒绝符号链接、Windows 目录联接和越界路径。scan 不改变 builds.watch 或 sync_include/sync_exclude；需要同步的默认排除目录仍须单独设置 sync_include。pnpm packages 支持静态块列表或带引号的内联列表，不求值 YAML 标签、锚点或动态表达式。

根级 mise 是安装和执行版本的依据。两份根级 mise 对同一工具声明不一致仍阻塞准备。参与范围内的包版本要求、Java 编译目标和子目录 mise 声明用于兼容性校验：根 Node 24.22.0 满足 24.x，低于要求或无法可靠验证的约束会明确阻塞；Java 编译目标按最低 JDK 要求检查。.nvmrc、.tool-versions、Volta、devEngines 等开发版本偏好不覆盖根声明，差异给出警告。没有根声明时可从已知范围推导能证明兼容的选择器，同优先级显式版本冲突仍需人工解决；实际准备前仍须有根级声明。

scan/init/check 的 JSON 含 scope：scopes 记录目录及 root/workflow/workspace/discovery/scan.roots 纳入原因，files 为本次元数据清单，truncated 为兜底发现跳过的目录。check 另有 warnings，不表示已安装或启动。每次计划内复用目录清单和元数据解析结果；下一次检查重新观察新增、删除和修改，不使用持久扫描缓存。

## 自动依赖维护

默认 `dependency_mode = "auto"`。启动以及运行期间的声明变化都会触发准备，按模块判断依赖变化；修改服务参数不会重新安装依赖，无需手动 prepare。已知标准 npm、pnpm、Yarn、Bun 和 uv 安装任务可自动生成或更新锁文件，再执行原来的安装任务；仅锁文件变化时保留锁定版本，不主动升级到最新版本。Maven 按 POM 重新安装；自动生成的 pip 流程重建项目内 .venv，uv sync 精确同步依赖。任意自定义脚本仍按原命令执行，额外包选择或自定义参数不会被改写，需要自行实现清理语义。

WSL 在 Linux 工作目录中解析锁文件，检查源码未被并发修改后只回写已知锁文件；依赖目录和缓存仍留在目标平台。更新依赖可能短暂停服，失败后监控继续等待修复；显式 prepare 可手动重试。设置 `dependency_mode = "locked"` 可要求已有锁文件，禁止自动解析和回写。

`init` 生成的配置含 `discovery = "auto"`：注册时记录自动发现基线，后续新增、删除模块会合并到任务、服务和构建图中；只更新仍与原生成值相同的字段，保留手改命令和主动删除的条目。已有自定义配置默认 `discovery = "manual"`，不会改写；首次改为 auto 时先采用当前配置并建立基线。自动配置更新会重新序列化 TOML，保留字段值，但不保留原注释和排版。

无法推断的入口、冲突以及新语言缺少根级 mise 版本声明时会提示解决，不猜测工具版本或业务服务。`scan` 和 `check` 只读分析，`init` 只创建缺失配置，三者均不执行安装。

## 命令与平台覆盖

推荐使用 argv 数组；每项是一个参数，不进行 shell 字符串拼接：

```toml
[tasks.install]
command = ["npm", "ci"]
tools = ["node"]
```

需要 shell 表达式时，command 使用字符串，并明确指定 bash 或 pwsh。
argv 数组不能同时设置 shell。任务和服务均可使用 platforms.windows / platforms.wsl 覆盖：

```toml
[tasks.message]
command = "printf '%s' ready"
shell = "bash"

[tasks.message.platforms.windows]
command = "Write-Output ready"
shell = "pwsh"
```

目标平台必须已经具有所选 shell。覆盖表中的 env 按键合并，其他字段整项替换。
CLI 的平台参数为 win/wsl；覆盖表继续使用 windows/wsl。

命令数组支持以下占位符；脚本字符串不展开这些占位符：

| 占位符 | 解析结果 |
|---|---|
| {python} | Windows 的 python 或 WSL 的 python3，mise 模式下使用项目版本 |
| {venv_python} | 本平台 .venv 中的 Python；根目录由 python.root 或命令 workdir 决定 |
| {maven} | 配置 java 选项时优先使用本平台 Maven Wrapper，否则使用 mvn |
| {spring_classpath} | 已准备的 Spring DevTools、依赖模块 class 覆盖目录和 trigger 目录 |

## 任务：tasks.NAME

| 字段 | 默认值或要求 | 含义 |
|---|---|---|
| command | 必填 | argv 数组，或搭配显式 shell 的脚本 |
| shell | 空 | 仅脚本命令使用 bash/pwsh |
| workdir | . | 相对项目根的工作目录 |
| depends_on | [] | 先执行的任务名称 |
| role | prepare | prepare 自动纳入准备；build 通常由构建策略引用 |
| manager | 空 | 包管理器标识，用于准备计划的锁文件校验 |
| tools | [] | 所需工具，例如 node、python、uv、java、maven |
| env | 空表 | 当前任务的环境变量，值必须为字符串 |
| java / python | 空表 | 下述原生驱动选项 |

prepare 会执行所有 role=prepare 任务，以及服务 prepare 列表和这些任务的依赖。
role=build 任务被服务 prepare 或任务依赖明确引用时，也会进入准备计划。
任务按依赖顺序执行，输出写入本机状态目录的 logs/tasks/NAME.log。

manager 为 npm/pnpm/yarn/bun/uv 时，准备计划要求 workdir 中存在相应锁文件。
显式工作流的工具需求以 tasks/services 为准；目录中残留的 uv.lock 不会使 pip 工作流额外要求 uv。扫描仍报告元数据解析错误及版本冲突，实际使用的工具必须在根级 mise 中声明。
自动扫描、发现和依赖指纹默认跳过 `.local/`，显式项目范围可以纳入本地源码；文件监控及默认 WSL 同步保留各自的排除规则。实际项目范围内的版本冲突会列出版本和来源文件。
发现工作区时，在包工作区根安装，在具体应用目录运行；自定义声明需要明确设置 workdir。
锁文件校验不会自动把任意 install 命令变为冻结安装，应在 command 中声明 npm ci、--frozen-lockfile、--immutable 或 uv sync --locked。

## 服务：services.NAME

| 字段 | 默认值或要求 | 含义 |
|---|---|---|
| driver | process | process 原生进程；compose 容器编排 |
| command | process 必填 | 与任务相同的命令格式；compose 可省略 |
| shell / workdir / tools / env | 与任务相同 | 命令执行设置 |
| prepare | [] | 服务需要的准备任务 |
| depends_on | [] | 先启动并等待就绪的服务 |
| restart | on-failure | never、on-failure 或 always |
| required | true | 是否参与项目就绪聚合；可选服务仍默认启动 |
| health | 空表 | TCP/HTTP 健康检查 |
| java / python / compose | 空表 | 原生驱动选项 |

服务依赖与任务依赖是两个独立的图。start --service 会包含服务依赖；stop 停止整个项目。
在当前项目目录中，可通过 `dev-tools logs --service __sync` 或 `dev-tools logs --service __watch` 查看监控日志；也可显式指定项目名，例如 `dev-tools logs my-app __watch`。

health 支持：

- tcp：1～65535 的端口，探测 127.0.0.1。
- http：以 http:// 或 https:// 开头的 URL；要求 2xx，直连且不跟随重定向。
- json：可选的非空 JSON 字段表，需同时配置 http；响应必须包含并匹配这些字段和类型，允许额外字段。最多读取 64 KiB，读取期间检查总时限，避免持续发送少量数据拖住查询；不将响应保存到状态或日志。
- timeout：等待就绪的秒数，默认 15，必须大于 0 且不超过 300。

- details：可选 JSON 对象字段路径数组，使用 `/` 分层及 JSON Pointer 的 `~0/~1` 转义，只遍历对象字段。需同时配置 http，最多 16 项、每项最多 128 字符和 8 层。例如 `details = ["/checks/storage", "/checks/queue"]`。仅提取值为 false 或固定失败状态（failed/error/unhealthy/unavailable/down）的字段名；不保存响应值或正文。即使 HTTP 非 2xx 也可读取，仍受 64 KiB 和读取时限约束。详情仅作诊断，不改变已有就绪判定。

同时声明 TCP 和 HTTP 时，两者都需要成功。原生进程没有探针时 health 为 unknown；进程就绪与已确认健康分别表达。Compose 还会根据容器运行状态和容器报告的健康状态判定，单次容器状态查询最多等待 5 秒，失败或超时视为不健康。

## 桌面客户端与共享服务

桌面应用可参考 [dev-desktop.toml](../examples/dev-desktop.toml)。使用手写 discovery=manual 工作流，frontend.depends_on=["backend"]、desktop.depends_on=["frontend"]，逐个等待依赖就绪。服务同时声明 tcp 和 http/json：新服务启动前拒绝已占用的声明端口，不接管或终止占用者；HTTP JSON 校验应用身份和 readiness。应用自行校验本项目会话，dev-tools 不读取令牌或应用会话文件。

```toml
[services.backend.health]
tcp = 7877
http = "http://127.0.0.1:7877/health"
timeout = 180
json = { application = "desktop-app", ready = true }

[services.desktop]
command = ["npm", "run", "tauri:dev"]
workdir = "frontend"
depends_on = ["frontend"]
tools = ["node", "rust"]
required = false
restart = "never"
```

required=false 不排除默认启动，但客户端关闭后不使共享服务失去就绪状态；客户端若被必需服务依赖，仍按必需依赖检查。restart=never 不在退出后自动重开。可选服务启动失败时，已启动的共享服务和监控继续运行，显式 start 返回 1；后台准备恢复失败的可选客户端只记录服务错误。维护不会重开已关闭的可选客户端。

上述规则适用于任意服务名称与驱动。必需依赖即使配置 required=false，也必须保持运行并通过就绪检查，退出码 0 不能代替就绪。必需服务或监控启动失败时，仅回滚本次新启动的 worker，保留此前已运行的服务；恢复记录同时保留启动错误和清理错误，显式 stop 取消待恢复的服务。

仅包含可选服务时，客户端关闭后，只要准备有效且维护监控仍在运行，项目仍显示为运行；stop 同时停止服务和维护。

```text
dev-tools start desktop-app
dev-tools start desktop-app --service frontend
dev-tools start desktop-app --service desktop
dev-tools stop desktop-app
```

第二条只启动或保留共享服务，第三条重开客户端并确保其依赖运行；重复 start 保留活动服务，不重复创建实例。未配置 desktop.health 时，running 仅表示启动器仍存活；需要确认 CDP 可为 Windows 桌面服务配置 tcp=9222、http="http://127.0.0.1:9222/json/version"。显示、CDP 绑定和首次 Rust 编译由应用/Tauri 管理，WSL 原生桌面开发还需要可用的图形会话及应用要求的系统库。

各服务已有 env 原样传递项目配置，不展开 shell 变量；应用入口自行解释共享后端模式、源码目录及 .local/data、.local/dev、.local/reports。开发客户端连接共享后端，发布版 sidecar 的所有权仍由应用决定。Tauri 自己监听 Rust 源码并重启客户端，无需 dev-tools 再执行相同构建。运行时版本与依赖声明变化仍按项目准备计划维护。
任务与服务还会收到 DEV_TOOLS_SOURCE_ROOT（源码目录）和 DEV_TOOLS_PROJECT_ROOT（原生运行工作区）的绝对路径；两者由本机绑定生成，不受项目 env 覆盖。Windows 通常相同，WSL 可能不同。应用可以据此选择数据和会话位置，dev-tools 不据此创建业务目录或保存令牌。

Windows 服务和准备子进程隐藏控制台，每个 worker 通过独立 Job Object 管理后代，窗口关闭或 stop 不会跨服务终止进程；WSL 使用原生进程组和 systemd 控制组。项目级 stop 停止本次受管的全部进程，释放其持有的系统文件锁，但不删除应用数据、锁文件或会话标记。
隐藏 worker 与图形窗口分别处理：Windows 子进程显式使用 SW_SHOWNORMAL，避免继承启动器的隐藏状态，同时通过 CREATE_NO_WINDOW 隐藏控制台；不额外增加 desktop 专用窗口开关，也不改变已经运行的窗口。

Rust 支持根级 mise 声明、rust-toolchain/rust-toolchain.toml 与 Cargo.toml 的 rust-version 最低要求，及 Cargo workspace 成员。已纳入的前端目录会识别 src-tauri/Cargo.toml，范围原因为 tauri；.local 中的旧副本仍默认排除。cargo/rustc 命令识别为 Rust 工具需求；通过 npm 启动 Tauri 时显式声明 tools=["node","rust"]，npm ci 安装项目内 Tauri CLI，dev-tools 不执行 npm 脚本推测工具，也不创建 Cargo 安装任务或自动升级编译器。

## 原生驱动选项

### Python

```toml
[services.api.python]
root = "."
```

root 相对项目根，指定包含 .venv 的目录；省略时以命令 workdir 为基准。
Windows 使用 .venv/Scripts/python.exe，WSL 使用 .venv/bin/python。
虚拟环境由准备任务创建或同步；启动不会执行包同步。uv 项目可参考 [dev-python-web.toml](../examples/dev-python-web.toml)。

### Maven 与 Spring

```toml
[tasks.install-java.java.maven]
repository = "user"

[services.api.java.maven]
repository = "user"

[services.api.java.spring]
devtools = "org.springframework.boot:spring-boot-devtools:3.5.0"
classpath_modules = ["backend/library/target/classes"]
classpath_entries = []
```

Maven 选项需分别配置在相关任务与服务上。repository 支持：

- user：运行用户本机的 ~/.m2/repository，默认值。
- project：按本机存储身份隔离的 ~/.cache/dev-tools/maven 下仓库；重命名保留该身份。

配置 java 选项后，{maven} 从 workdir 向项目根查找本平台的 mvnw.cmd 或 mvnw，再回退到 mvn。
没有 Wrapper 时，mise 项目需要在根级声明 Maven 版本；system 项目使用已有 Maven。

Spring devtools 是 group:artifact:version 坐标，示例版本应与目标项目的 Spring Boot 元数据匹配。
自动发现无法确定版本时生成 unresolved 诊断；需要解决后才能 prepare。
JAR 由统一准备流程补齐，start 和后台依赖维护可自动准备；普通局部构建不下载。

classpath_modules 是相对项目根的已编译输出目录。
classpath_entries 可筛选这些目录中的第一层目录名称；[] 表示不筛选。
仅 .class 文件复制到原生状态目录，逐文件原子替换，复制完成后更新统一 trigger 文件。
应用启动命令需引用 {spring_classpath} 并配置相同的 dev-tools-reload.trigger；完整例子见 [dev-java-web.toml](../examples/dev-java-web.toml)。

### Compose

```toml
[services.stack]
driver = "compose"

[services.stack.compose]
files = ["compose.yaml"]
profiles = []
build = true
pull = false
```

files 相对服务 workdir，默认 ["compose.yaml"]；profiles 指定 Compose profile。
统一准备流程根据 pull/build 准备镜像，默认不 pull、执行 build；start 可自动补齐准备。
存储身份产生稳定的 Compose project name。
Windows 需要已配置的原生 Docker 引擎；WSL prepare 可以补齐所请求的 Docker/Compose。
Redis/MySQL 等业务服务应写在项目自己的 Compose 声明中，不由扫描器推断。

## 构建监控：builds.SERVICE

```toml
[builds.api]
watch = ["backend/app/src/main", "backend/library/src/main", "backend/pom.xml"]
source_task = "compile-java"
resource_task = "resources-java"
structural_task = "clean-java"
extensions = [".java", ".xml", ".yaml", ".yml", ".properties"]
resources = [".xml", ".yaml", ".yml", ".properties"]
structural = ["pom.xml", "**/pom.xml", ".mvn/**", "**/.mvn/**"]
```

SERVICE 必须是已声明的服务，三个 task 字段必须引用已有任务；通常将这些任务声明为 role=build。
watch 必须非空，可填写相对项目根的文件或目录。extensions/resources/structural 可省略并使用默认值；上面的 structural 只是自定义示例，默认还包含 Maven Wrapper 文件模式。

- extensions 过滤监控文件的扩展名；匹配 structural 模式的文件也会被纳入。
- 新增、删除文件或匹配 structural 模式的变化触发结构构建。
- resources 扩展名或 resources 目录中的变化触发资源构建。
- 其他已监控文件变化触发源码构建。

源码改动监控读取当前平台的运行目录：Windows 直接读取源码，WSL 读取同步后的 Linux 工作目录。项目声明和 Git 状态仍从原源码目录读取。WSL 的 watch 路径需要包含在同步范围内；维护或构建完成后重新记录工作目录快照，避免重复构建。

空闲时同步轮询的等待从 0.75 秒逐步增加到最多 3 秒；原生文件监控从 0.5 秒增加到最多 1.5 秒，发现变化后恢复快速检查，扫描和构建耗时另计。WSL 同步进程将 Git/依赖指纹检查结果写入原生状态目录，监控进程复用新鲜结果；结果缺失、超过 6 秒、属于上次启动或早于本次维护时重新检查。文件变化触发构建或维护前，再次直接确认 Git 和依赖状态，避免缓存掩盖刚开始的 checkout。仍定期完整扫描依赖声明与同步范围，包含新增、删除、升级和未提交文件；不依赖跨文件系统的文件事件通知。rsync 的文件变化、目录创建或删除决定快速轮询，仅目录元数据变化不维持快速轮询；不改变 include/exclude、原生编译目录或依赖准备流程。空闲后的首个改动可能多等几秒。

源码构建保持进程运行；资源和结构构建停止受影响服务与依赖方，完成后恢复。
分支构建同步源码、执行准备任务和结构构建，使用已经准备的运行时。
手动执行 `dev-tools build [NAME]` 进行完整构建，或用 `--service SERVICE` 只构建指定服务。
源码、资源和结构变化由后台自动分类，无需手动指定类型。完整构建刷新项目依赖并执行结构构建；指定服务时停止并恢复受影响服务及依赖方。
服务局部构建保留原有准备指纹。运行时版本变化、模块重新发现或锁文件需要解析时，先执行 `start` 或 `prepare`，构建不会把未安装的版本标为已准备。
Compose 镜像只在未指定服务的完整构建中重建。

## 验证与修改流程

```text
dev-tools show --json
dev-tools check --json
dev-tools prepare
dev-tools start
dev-tools status --json
dev-tools logs my-app install --task
```

准备预览不执行任务、不安装运行时、不改变注册或缓存；平台适配器可能进行只读 Docker 查询。
未解决的扫描、初始化或准备计划返回 1。运行失败和部分结果返回 1，参数错误返回 2，中断返回 130；
status 的退出码不等于服务健康状态，自动化应检查其 JSON 内容。
config/sysinfo/report 的 JSON 各自用于该命令，不能假定它们包含项目命令的 schema_version。
完整字段、错误文档及退出码约定见 [CLI 与自动化接口](cli-contract.md)。

更多声明模板见 [examples](../examples/README.md)，两侧安装与偏好设置见 [README](../README.md)。
