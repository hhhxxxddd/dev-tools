# Scoop's shim can be invoked from cmd.exe or Windows PowerShell 5.1.
& pwsh.exe -NoProfile -File (Join-Path $PSScriptRoot 'dev-tools.ps1') @args
exit $LASTEXITCODE
