# Scoop bucket

此目录的 `dev-tools.json` 从正式 Release 附件同步，使用已发布 ZIP 的真实 SHA256。
发行工作流生成附件，不自动修改 bucket；维护者在发布成功后同步清单。不要使用开发候选包的校验值。
清单安装 Git、PowerShell 7、mise 和控制程序宿主；WSL 使用 `dev-tools self update -e wsl` 单独部署或更新。

This directory's `dev-tools.json` is synchronized from official release assets with the
published ZIP's actual SHA256. The release workflow produces assets without modifying
the bucket; maintainers synchronize it after publication. Do not use candidate checksums.
The manifest installs Git, PowerShell 7, mise and the controller host. Deploy or update WSL separately with `dev-tools self update -e wsl`.

```powershell
scoop bucket add dev-tools https://github.com/hhhxxxddd/dev-tools
scoop install dev-tools/dev-tools
scoop update dev-tools
```
