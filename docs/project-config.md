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

在 Windows 执行 `dev-tools register`、`dev-tools prepare --dry-run --json`，检查后执行 prepare 和 start。
原生 WSL 修改操作使用 sudo；预览无需 sudo。

## 根字段

| 字段 | 默认值或要求 | 含义 |
|---|---|---|
| schema | 必须为整数 1 | 声明格式版本 |
| name | 必填 | 项目默认注册名 |
| toolchain | mise | mise 使用根级显式版本；system 使用已有系统工具 |
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
| health | 空表 | TCP/HTTP 健康检查 |
| java / python / compose | 空表 | 原生驱动选项 |

服务依赖与任务依赖是两个独立的图。start --service 会包含服务依赖；stop 停止整个项目。
监控日志可以通过 `dev-tools logs __sync` 或 `dev-tools logs __watch` 查看。

health 支持：

- tcp：1～65535 的端口，探测 127.0.0.1。
- http：以 http:// 或 https:// 开头的 URL。
- timeout：等待就绪的秒数，默认 15，必须大于 0 且不超过 300。

同时声明 TCP 和 HTTP 时，两者都需要成功。原生进程没有探针时 health 为 unknown；进程就绪与已确认健康分别表达。Compose 还会根据容器运行状态和容器报告的健康状态判定。

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
JAR 仅由显式 prepare 补齐，构建和启动不下载。

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
显式 prepare 根据 pull/build 准备镜像，默认不 pull、执行 build；start 使用准备好的镜像。
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

源码构建保持进程运行；资源和结构构建停止受影响服务与依赖方，完成后恢复。
分支构建同步源码、执行准备任务和结构构建，使用已经准备的运行时。
手动构建为 `dev-tools build [NAME] --kind source|resource|structural|branch`，默认 branch；
同时指定 --service 和默认 branch 时按该服务的 structural 构建处理。
省略 --service 时，source/resource/structural 执行全部构建策略中的对应任务及其依赖；只有 branch 自动加入准备任务。
source/resource/structural（包括指定 --service 的默认构建）保留原有准备指纹，不会把未准备或依赖已变化的项目标为已准备。
锁文件或项目声明变化后，局部构建不能替代 prepare；完整 branch 构建执行准备任务后可以更新准备指纹，但不会安装运行时或 Spring DevTools JAR。
Compose 镜像只在未指定 --service 的 branch/structural 构建中重新构建。

## 验证与修改流程

```text
dev-tools show --json
dev-tools prepare --dry-run --json
dev-tools prepare
dev-tools start
dev-tools status --json
dev-tools logs install --task
```

准备预览不执行任务、不安装运行时、不改变注册或缓存；平台适配器可能进行只读 Docker 查询。
未解决的扫描或准备计划返回 2。执行失败的工具自身错误通常返回 1，参数错误返回 2；
status 的退出码不等于服务健康状态，自动化应检查其 JSON 内容。
config/sysinfo/report 的 JSON 各自用于该命令，不能假定它们包含项目命令的 schema_version。

更多声明模板见 [examples](../examples/README.md)，两侧安装与偏好设置见 [README](../README.md)。
