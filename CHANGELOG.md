# 变更记录

## 0.4.0 - 2026-10-09

### 发行与改进

- 精简中英文 README，突出依赖发现、热部署和完整命令示例；配置、运行和开发细节移到 docs。
- 修复两平台打包排序与换行差异，WSL 部署包含 README 引用的更新记录；支持准备草稿后发布并更新 Scoop bucket。

- 增加 self install/update/status：从 Windows 同版本部署 WSL；原生 WSL 可校验最新 Release 后更新；总览帮助提示缺失部署。
- 增加可重复构建的版本包、SHA256、Scoop 清单和发行工作流；Scoop shim 替代旧 profile 标记块，更新/卸载检查活动 Windows worker。
- WSL 更新拒绝覆盖活动 worker 使用的程序；安装和更新保留本机注册、偏好、状态、缓存与项目运行时。
- list 默认以名称、环境、状态三列表格显示项目，按本机语言配置翻译并对齐中英文列；JSON 输出保持原结构。
- 修复局部构建误更新准备指纹的问题：source/resource/structural 和指定服务的默认构建保留原状态，依赖变化后仍要求 prepare；完整 branch 构建继续执行准备任务并更新指纹。
- 增加 sync_include，在保留原生依赖和输出的同时允许同步同名源码目录，例如 frontend/build。
- 修复 POSIX Maven 将仓库参数中的引号当成路径内容的问题。
- 修复未指定服务的 source/resource/structural 构建误用准备任务和结构构建任务；Compose 镜像只在 branch/structural 构建中重建。
- prepare 恢复活动服务时补齐当前声明中的传递依赖，依赖启动失败仍保留恢复记录。
- 合并两份根级 mise 配置的工具清单，继续拒绝同优先级版本冲突。
- register --force 支持为同一源码和运行用户添加独立别名，保留冲突绑定；WSL 按解析后的运行用户检查重复注册。
- 统一指纹、文件监控和 Maven Wrapper 解析中的路径形式，修复 Windows 长短路径造成的测试与集成结果差异。
- 显式 pip 工作流不再被残留 uv.lock 的 uv 版本诊断阻断，实际使用的工具仍需根级声明。
- WSL 入口在 systemd 未设置 HOME 时从账户信息定位本机配置。
- Windows 后台 worker 沿用控制进程解析后的注册和状态目录，避免 MSIX 宿主与原生后台进程的目录差异导致启动失败。
- WSL 真实生命周期测试使用独立的临时服务名，避免与已安装的 systemd 服务模板冲突。

### 统一项目核心

- Windows/WSL 共用项目声明、发现、准备计划、任务执行、服务生命周期和监控 worker；平台适配器只负责原生存储、用户、同步、进程与 systemd。
- 用项目根 dev-tools.toml 描述任意命名的任务、服务、依赖图和构建策略；JSON schema 升为 3，项目 TOML schema 为 1。
- 独立准备混合项目的 Node、Maven/Spring 和 Python 工作流，使用平台原生依赖、虚拟环境与缓存。
- 静态 scan/init 保留现有声明；只允许显式 prepare 安装根级 mise 运行时、平台依赖和框架产物。
- 两侧共用健康检查、准备指纹、操作锁、失败恢复、分支监控和热构建；重命名保留原生存储身份。
- WSL 使用独立部署、私有 Python 宿主和通用 systemd 模板；不依赖另一个仓库，不读取或迁移旧 wsl-devctl 注册。

### CLI 与本机配置

- 项目命令提升到顶层，环境参数使用 -e win/wsl，省略时选择当前平台；跨环境调用转换路径并保留当前目录。
- 支持在唯一匹配的项目目录及子目录中省略名称；提供 list、show、rename、unregister，构建入口统一为 build。
- PowerShell/Bash 共用命令定义和完整帮助；status 专用于项目状态，机器信息使用 sysinfo。
- 移除旧机器级 doctor/status 包装；self 和安装器只部署控制程序宿主与入口，不管理用户全局 mise 默认配置。
- 增加用户本机 TOML 配置和 config/edit/check；sysinfo、report、WSL 发行版与编辑器统一从配置解析。
- 默认中文，语言仅通过配置切换 zh/en；默认编辑器为 Windows 记事本和 WSL vi。
- 中英帮助与诊断集中管理；跨环境传递显示语言，保留第三方输出、JSON 字段和状态标识。
- sysinfo 的有序清单控制说明与只读探测；report 支持根目录、采集器、深度、超时和刷新设置，原 sysinfo/report JSON 配置不再读取。

### 文档与验证

- 重写中英文 README，统一安装、工作流、命令、本机配置、平台边界和开发验证说明。
- 增加中英文项目配置参考与示例索引，整理 TOML 模板；WSL 部署包含所引用的文档和示例。
- 验证两平台相同的真实服务、热构建、资源重启、准备失败恢复和重命名场景，并增加双向调用与配置/语言传输验证。
- 修正 bootstrap 的机器诊断提示，使用 sysinfo 和 mise doctor。

## 0.2.0 - 2026-09-03

- Windows 和 WSL 增加 dev-tools --version。
- WSL bootstrap 以 root 执行特权安装，使安装无需交互式 sudo。
- PowerShell 传入 WSL 的内嵌 Bash 脚本统一为 LF 换行。
