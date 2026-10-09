# Scoop bucket

发布工作流从版本化 ZIP 的真实 SHA256 生成 `dev-tools.json`，并更新到仓库默认分支。
首次 Release 发布前这里没有可安装的清单；不要把开发包的校验值当作已发布版本。
清单安装 Git、PowerShell 7、mise 和控制程序宿主；WSL 使用 `dev-tools self update -e wsl` 单独部署或更新。

The release workflow generates `dev-tools.json` from the actual versioned ZIP SHA256
and updates the repository's default branch. The first release must be published before
this bucket is installable.
The manifest installs Git, PowerShell 7, mise and the controller host. Deploy or update WSL separately with `dev-tools self update -e wsl`.

```powershell
scoop bucket add dev-tools https://github.com/hhhxxxddd/dev-tools
scoop install dev-tools/dev-tools
scoop update dev-tools
```
