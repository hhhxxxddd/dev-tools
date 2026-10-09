# 变更记录

## 0.4.0 - 2026-10-09

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
- 移除操作型 CLI 安装/更新管理及机器级 doctor/status 包装，安装器仅准备控制程序宿主和入口，不管理用户全局 mise 默认配置。
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
