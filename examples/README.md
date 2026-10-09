# 项目声明示例 / Project declaration examples

[中文指南](../README.md) · [English guide](../README.en.md)

这些文件是共享 schema 1 的声明模板，可复制到目标项目根目录并命名为 dev-tools.toml。
使用前核对应用目录、包管理器、锁文件、服务端口和框架版本，运行时版本另写在项目根 mise 文件中。
Templates use shared schema 1. Copy one to the project root as dev-tools.toml, review application paths,
package managers, lockfiles, ports and framework versions, and declare runtime versions in root mise files.

| 模板 / Template | 适用项目 / Project |
|---|---|
| [dev-generic.toml](dev-generic.toml) | Python 虚拟环境与自定义 app 模块 / Python venv and a custom app module |
| [dev-next.toml](dev-next.toml) | npm 锁文件与 Next.js dev 服务 / npm lockfile and a Next.js dev service |
| [dev-python-web.toml](dev-python-web.toml) | uv 锁文件与 Uvicorn 服务 / uv lockfile and a Uvicorn service |
| [dev-java-web.toml](dev-java-web.toml) | Maven/Spring 多模块后端与 npm 前端 / Maven/Spring backend modules and an npm frontend |
| [dev-docker-compose.toml](dev-docker-compose.toml) | 已有 Compose 声明 / Existing Compose declarations |

声明复制并核对后，先注册，再预览准备计划：
After reviewing the declaration, register and preview preparation:

```text
dev-tools register
dev-tools prepare --dry-run --json
```

确认后执行 prepare/start；原生 WSL 的修改操作使用 sudo。预览不会运行项目命令或安装工具。
After review, run prepare/start. Native WSL mutations require sudo. Previews do not run project commands or install tools.

字段说明：[中文](../docs/project-config.md) / [English](../docs/project-config.en.md)。
dev-tools 自身的偏好模板为 [settings.example.toml](../config/settings.example.toml)，不应复制成项目声明。
The controller preference template is [settings.example.toml](../config/settings.example.toml); it is not a project declaration.
