[CmdletBinding()]
param([string] $Distro = 'Ubuntu')

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'verify-source.ps1')
Assert-DevToolsSource $repoRoot

if (-not (Get-Command mise -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command scoop -ErrorAction SilentlyContinue)) {
        throw 'Windows mise is missing and Scoop is unavailable. Install Scoop or mise first.'
    }
    & scoop install mise
    if ($LASTEXITCODE -ne 0) { throw "Scoop failed to install mise (exit $LASTEXITCODE)." }
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}
& (Join-Path $PSScriptRoot 'install.ps1') -Distro $Distro
& (Join-Path $PSScriptRoot 'install-wsl.ps1') -Distro $Distro
Write-Host 'Bootstrap complete. Restart PowerShell or run: . $PROFILE'
Write-Host 'Command overview: dev-tools help'
