[CmdletBinding()]
param(
    [string] $Distro = 'Ubuntu',
    [switch] $InstallWslDevctl,
    [string] $WslDevctlPath,
    [switch] $SkipRuntimeInstall
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$parentRoot = Split-Path -Parent $repoRoot

function Invoke-Native {
    param(
        [Parameter(Mandatory)][string] $FilePath,
        [Parameter(ValueFromRemainingArguments)][string[]] $Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE."
    }
}

function Refresh-ProcessPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user"
}

function Test-WslCommand {
    param([Parameter(Mandatory)][string] $Command)

    & wsl.exe -d $Distro -- bash -lc "command -v $Command >/dev/null 2>&1"
    return $LASTEXITCODE -eq 0
}

function Convert-ToWslPath {
    param([Parameter(Mandatory)][string] $WindowsPath)

    $portablePath = $WindowsPath -replace '\\', '/'
    $output = & wsl.exe -d $Distro -- wslpath -a $portablePath
    $value = if ($null -ne $output) { "$($output | Select-Object -First 1)".Trim() } else { '' }
    if ($LASTEXITCODE -ne 0 -or -not $value) {
        throw "Cannot map Windows path into WSL: $WindowsPath"
    }
    return $value
}

function Invoke-WslInstaller {
    param([Parameter(Mandatory)][string] $WslRepositoryPath)

    $rawUserId = & wsl.exe -d $Distro -- id -u
    $userId = if ($null -ne $rawUserId) { "$rawUserId".Trim() } else { '' }
    if ($LASTEXITCODE -ne 0 -or $userId -notmatch '^\d+$') {
        throw "Cannot determine the active user in WSL distro: $Distro"
    }
    $arguments = @('-d', $Distro, '--')
    if ($userId -eq '0') {
        $arguments += @('bash', "$WslRepositoryPath/scripts/install.sh")
    } else {
        $arguments += @('sudo', 'bash', "$WslRepositoryPath/scripts/install.sh")
    }
    Invoke-Native -FilePath wsl.exe -Arguments $arguments
}

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw 'WSL is not available on this Windows installation.'
}
$distros = @(& wsl.exe --list --quiet) | ForEach-Object { ("$_" -replace "`0", '').Trim() }
if ($Distro -notin $distros) {
    throw "WSL distro not found: $Distro"
}

if (-not (Get-Command mise -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command scoop -ErrorAction SilentlyContinue)) {
        throw 'Windows mise is missing and Scoop is unavailable. Install Scoop or mise first.'
    }
    Write-Host 'Installing Windows mise with Scoop...'
    Invoke-Native -FilePath scoop -Arguments @('install', 'mise')
    Refresh-ProcessPath
}

if (-not (Test-WslCommand mise)) {
    Write-Host "Installing mise in WSL distro $Distro with extrepo + apt..."
    $installMise = @'
set -euo pipefail
if ! command -v apt-get >/dev/null 2>&1; then
  printf 'Automatic mise installation requires an Ubuntu/Debian WSL distro.\n' >&2
  exit 1
fi
if [[ $(id -u) -eq 0 ]]; then
  sudo_cmd=()
elif command -v sudo >/dev/null 2>&1; then
  sudo_cmd=(sudo)
else
  printf 'Root or sudo is required to install mise.\n' >&2
  exit 1
fi
if ! command -v extrepo >/dev/null 2>&1; then
  "${sudo_cmd[@]}" apt-get update
  "${sudo_cmd[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y extrepo
fi
"${sudo_cmd[@]}" extrepo enable mise
"${sudo_cmd[@]}" apt-get update
"${sudo_cmd[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y mise python3
'@
    Invoke-Native -FilePath wsl.exe -Arguments @('-d', $Distro, '--', 'bash', '-lc', $installMise)
}

if (-not (Test-WslCommand python3)) {
    Write-Host "Installing Python 3 in WSL distro $Distro for the dev-tools installer..."
    $installPython = @'
set -euo pipefail
if [[ $(id -u) -eq 0 ]]; then
  sudo_cmd=()
elif command -v sudo >/dev/null 2>&1; then
  sudo_cmd=(sudo)
else
  printf 'Root or sudo is required to install Python 3.\n' >&2
  exit 1
fi
"${sudo_cmd[@]}" apt-get update
"${sudo_cmd[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y python3
'@
    Invoke-Native -FilePath wsl.exe -Arguments @(
        '-d', $Distro, '--', 'bash', '-lc', $installPython
    )
}

Write-Host 'Installing the PowerShell dev-tools entrypoint...'
$enableWslDevctlForwarder = $InstallWslDevctl -or (Test-WslCommand wsl-devctl)
$installArguments = @{
    Distro = $Distro
    EnableWslDevctlForwarder = [bool] $enableWslDevctlForwarder
}
& (Join-Path $PSScriptRoot 'install.ps1') @installArguments

$wslRepoRoot = Convert-ToWslPath $repoRoot
Write-Host "Installing the dev-tools entrypoint in WSL distro $Distro..."
Invoke-WslInstaller -WslRepositoryPath $wslRepoRoot

if (-not $SkipRuntimeInstall) {
    Write-Host 'Installing shared runtime declarations on Windows and WSL...'
    & (Join-Path $PSScriptRoot 'dev-tools.ps1') install -Distro $Distro
    if ($LASTEXITCODE -ne 0) {
        throw "dev-tools install failed with exit code $LASTEXITCODE."
    }
}

if ($InstallWslDevctl) {
    $resolvedWslDevctl = if ($WslDevctlPath) {
        [IO.Path]::GetFullPath($WslDevctlPath)
    } else {
        Join-Path $parentRoot 'wsl-devctl'
    }
    if (-not (Test-Path -LiteralPath $resolvedWslDevctl)) {
        Write-Host "Cloning wsl-devctl into: $resolvedWslDevctl"
        if (Get-Command gh -ErrorAction SilentlyContinue) {
            Invoke-Native -FilePath gh -Arguments @(
                'repo', 'clone', 'hhhxxxddd/wsl-devctl', $resolvedWslDevctl
            )
        } elseif (Get-Command git -ErrorAction SilentlyContinue) {
            Invoke-Native -FilePath git -Arguments @(
                'clone', 'https://github.com/hhhxxxddd/wsl-devctl.git', $resolvedWslDevctl
            )
        } else {
            throw 'Neither gh nor git is available to clone wsl-devctl.'
        }
    }
    $installer = Join-Path $resolvedWslDevctl 'scripts\install.sh'
    if (-not (Test-Path -LiteralPath $installer)) {
        throw "The selected directory is not a wsl-devctl checkout: $resolvedWslDevctl"
    }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw 'Git is required to verify the wsl-devctl checkout origin.'
    }
    $origin = (& git -C $resolvedWslDevctl remote get-url origin 2>$null).Trim()
    if (
        $LASTEXITCODE -ne 0 -or
        $origin -notmatch '^(?:git@github\.com:|https://github\.com/)hhhxxxddd/wsl-devctl(?:\.git)?$'
    ) {
        throw "Refusing to execute an unverified wsl-devctl checkout: $resolvedWslDevctl"
    }
    $wslDevctlRoot = Convert-ToWslPath $resolvedWslDevctl
    Write-Host "Installing wsl-devctl in WSL distro $Distro..."
    Invoke-WslInstaller -WslRepositoryPath $wslDevctlRoot
}

Write-Host ''
Write-Host 'Bootstrap complete.' -ForegroundColor Green
Write-Host 'Restart PowerShell or run: . $PROFILE'
Write-Host "Verify both runtimes with: dev-tools status"
if ($InstallWslDevctl) {
    Write-Host 'Chinese help is available with: wsl-devctl help'
}
