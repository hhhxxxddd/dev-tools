# Scoop bucket

发布工作流从版本化 ZIP 的真实 SHA256 生成 `dev-tools.json`，并更新到仓库默认分支。
首次 Release 发布前这里没有可安装的清单；不要把开发包的校验值当作已发布版本。

The release workflow generates `dev-tools.json` from the actual versioned ZIP SHA256
and updates the repository's default branch. The first release must be published before
this bucket is installable.

```powershell
scoop bucket add dev-tools https://github.com/hhhxxxddd/dev-tools
scoop install dev-tools/dev-tools
```
