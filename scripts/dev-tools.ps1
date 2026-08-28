[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string] $Command = 'status',

    [Parameter(Position = 1, ValueFromRemainingArguments)]
    [string[]] $Arguments,

    [string] $Distro = 'Ubuntu'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$configPath = Join-Path $repoRoot 'config\mise.toml'
$sourcePath = Join-Path $repoRoot 'src'
$windowsMiseDataDir = [Environment]::GetEnvironmentVariable('MISE_DATA_DIR', 'User')
$windowsMiseCacheDir = [Environment]::GetEnvironmentVariable('MISE_CACHE_DIR', 'User')

if (-not $windowsMiseDataDir) {
    $windowsMiseDataDir = Join-Path $env:LOCALAPPDATA 'mise'
}

function Show-Help {
    @'
dev-tools - 管理 Windows/WSL 开发运行时和项目版本声明

环境命令：
  dev-tools status               显示两端 mise 及当前工具版本
  dev-tools install              安装共享配置声明的缺失版本
  dev-tools outdated             检查可更新版本
  dev-tools upgrade              更新到配置范围内的最新版本
  dev-tools prune                清理不再引用的旧版本
  dev-tools doctor               诊断两端 mise 环境

项目命令：
  dev-tools project scan [PATH]           扫描项目版本声明
  dev-tools project scan [PATH] --json    输出机器可读结果
  dev-tools project init [PATH]           缺少时生成 mise.toml
  dev-tools project init [PATH] --dry-run 只预览，不写入

环境命令默认同时作用于 Windows 和 Ubuntu WSL；使用 -Distro 指定其他发行版。
'@ | Write-Host
}

if ($Command -in @('help', '-h', '--help')) {
    Show-Help
    exit 0
}

if (-not (Test-Path -LiteralPath $configPath)) {
    throw "Shared mise config not found: $configPath"
}
if (-not (Get-Command mise -ErrorAction SilentlyContinue)) {
    throw 'mise is not available on Windows. Install it with: scoop install mise'
}

function Use-WindowsMiseEnvironment {
    $env:MISE_GLOBAL_CONFIG_FILE = $configPath
    $env:MISE_DATA_DIR = $windowsMiseDataDir
    if ($windowsMiseCacheDir) {
        $env:MISE_CACHE_DIR = $windowsMiseCacheDir
    }
    $shimsPath = Join-Path $windowsMiseDataDir 'shims'
    if (($env:Path -split ';') -notcontains $shimsPath) {
        $env:Path = "$shimsPath;$env:Path"
    }
}

function Invoke-ProjectCommand {
    $previousConfig = $env:MISE_GLOBAL_CONFIG_FILE
    $previousDataDir = $env:MISE_DATA_DIR
    $previousCacheDir = $env:MISE_CACHE_DIR
    $previousPythonPath = $env:PYTHONPATH
    $previousPath = $env:Path
    try {
        Use-WindowsMiseEnvironment
        $python = (& mise -C $repoRoot which python).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $python) {
            throw 'Cannot resolve the shared mise Python runtime.'
        }
        $env:PYTHONPATH = $sourcePath
        & $python -m dev_tools.cli project @Arguments
        exit $LASTEXITCODE
    }
    finally {
        $env:MISE_GLOBAL_CONFIG_FILE = $previousConfig
        $env:MISE_DATA_DIR = $previousDataDir
        $env:MISE_CACHE_DIR = $previousCacheDir
        $env:PYTHONPATH = $previousPythonPath
        $env:Path = $previousPath
    }
}

if ($Command -eq 'project') {
    Invoke-ProjectCommand
}

$validCommands = @('status', 'install', 'sync', 'outdated', 'upgrade', 'prune', 'doctor')
if ($Command -notin $validCommands) {
    Write-Error "未知命令：$Command"
    Show-Help
    exit 2
}

$normalizedConfigPath = $configPath -replace '\\', '/'
if ($normalizedConfigPath -notmatch '^(?<Drive>[A-Za-z]):/(?<Rest>.+)$') {
    throw "Cannot map the shared mise config into WSL: $configPath"
}
$wslConfigPath = "/mnt/$($Matches.Drive.ToLowerInvariant())/$($Matches.Rest)"

function Invoke-WindowsMise {
    param([string[]] $MiseArgs)

    Write-Host "`n== Windows ==" -ForegroundColor Cyan
    $previousConfig = $env:MISE_GLOBAL_CONFIG_FILE
    $previousDataDir = $env:MISE_DATA_DIR
    $previousCacheDir = $env:MISE_CACHE_DIR
    $previousPath = $env:Path
    try {
        Use-WindowsMiseEnvironment
        & mise @MiseArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Windows mise failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        $env:MISE_GLOBAL_CONFIG_FILE = $previousConfig
        $env:MISE_DATA_DIR = $previousDataDir
        $env:MISE_CACHE_DIR = $previousCacheDir
        $env:Path = $previousPath
    }
}

function Invoke-WslMise {
    param([string[]] $MiseArgs)

    Write-Host "`n== WSL: $Distro ==" -ForegroundColor Cyan
    $wslPath = '/root/.local/share/mise/shims:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
    & wsl.exe -d $Distro -- env "MISE_GLOBAL_CONFIG_FILE=$wslConfigPath" "PATH=$wslPath" mise @MiseArgs
    if ($LASTEXITCODE -ne 0) {
        throw "WSL mise failed with exit code $LASTEXITCODE."
    }
}

function Invoke-Both {
    param([string[]] $MiseArgs)

    Invoke-WindowsMise -MiseArgs $MiseArgs
    Invoke-WslMise -MiseArgs $MiseArgs
}

switch ($Command) {
    'status' {
        Invoke-Both -MiseArgs @('--version')
        Invoke-Both -MiseArgs @('ls', '--current')
    }
    { $_ -in @('install', 'sync') } {
        Invoke-Both -MiseArgs @('--yes', 'install')
    }
    'outdated' { Invoke-Both -MiseArgs @('outdated') }
    'upgrade' { Invoke-Both -MiseArgs @('--yes', 'upgrade') }
    'prune' { Invoke-Both -MiseArgs @('--yes', 'prune', '--tools') }
    'doctor' { Invoke-Both -MiseArgs @('doctor') }
}
