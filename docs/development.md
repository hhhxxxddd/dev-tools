# 开发与验证

[English](development.en.md) · [返回 README](../README.md)

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

入口改动还应验证 `scripts/dev-tools.ps1` 与 `scripts/dev-tools`；bootstrap 改动先解析所有 PowerShell 安装脚本。维护约束见 [AGENTS.md](../AGENTS.md)。

真实验证脚本以临时注册、状态和缓存运行：`tests/integration/lifecycle_smoke.py` 检查 HTTP 服务、热构建、恢复和重命名；`toolchain_smoke.py` 使用已安装的 Java/Node/Python/Maven；`transport_smoke.py` 从 Windows 检查双向路径、隐式名称、帮助、配置和语言传递。WSL 生命周期测试需要 root 和 systemd，工具链测试不安装新版本；双向测试需要两侧入口已安装。

[MIT License](../LICENSE)
