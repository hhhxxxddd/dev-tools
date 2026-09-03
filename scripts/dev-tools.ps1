[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string] $Command = 'help',

    [Parameter(Position = 1, ValueFromRemainingArguments)]
    [string[]] $Arguments,

    [string] $Distro = 'Ubuntu'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$sourcePath = Join-Path $repoRoot 'src'
$internalPython = 'python@3.11'
$cliConfigPath = Join-Path $repoRoot 'config\windows-cli-tools.toml'

function Show-Help {
    @'
dev-tools - 按项目发现并准备 Windows/WSL 开发工具版本

帮助：
  dev-tools help                         显示本页
  dev-tools --version                    显示当前版本
  dev-tools project --help               显示项目子命令
  dev-tools project <命令> --help        显示某个项目命令的完整参数

环境检查：
  dev-tools status                       显示 Windows 与 WSL 的 mise 状态
  dev-tools doctor                       运行 Windows 与 WSL 的 mise 诊断

Windows 操作型 CLI：
  dev-tools cli status                   查看 Codex、Claude Code、CodeGraph
  dev-tools cli install                  显式安装缺失的 CLI 及其宿主 Node
  dev-tools cli outdated                 检查这些 CLI 的可更新版本
  dev-tools cli upgrade                  显式更新这些 CLI

项目工作流：
  dev-tools project scan [PATH]           只扫描并报告版本声明
  dev-tools project scan [PATH] --json    输出机器可读结果
  dev-tools project init [PATH]           缺少时生成项目级 mise.toml
  dev-tools project init [PATH] --dry-run 预览生成内容
  dev-tools project prepare [PATH]        安装该项目声明的缺失版本
  dev-tools project prepare [PATH] --dry-run
                                         只显示将安装的版本

dev-tools 不为项目维护或安装全局默认版本。Windows 操作型 CLI 与其宿主 Node 独立管理，
不会被 project prepare 当作项目声明安装。
status/doctor 默认检查 Windows 和 Ubuntu WSL；使用 -Distro 指定其他发行版。
'@ | Write-Host
}

function Assert-Mise {
    if (-not (Get-Command mise -ErrorAction SilentlyContinue)) {
        throw 'mise is not available on Windows. Install it with: scoop install mise'
    }
}

function Get-InternalPython {
    Assert-Mise
    $pythonRoot = "$(& mise where $internalPython 2>$null | Select-Object -First 1)".Trim()
    $python = if ($pythonRoot) { Join-Path $pythonRoot 'python.exe' } else { $null }
    if ($LASTEXITCODE -ne 0 -or -not $python -or -not (Test-Path -LiteralPath $python)) {
        throw 'dev-tools internal Python is missing. Rerun scripts\install.ps1.'
    }
    return $python
}

function Invoke-InternalCli {
    param([Parameter(Mandatory)][string[]] $CliArguments)

    $python = Get-InternalPython
    $previousPythonPath = $env:PYTHONPATH
    $previousPythonUtf8 = $env:PYTHONUTF8
    try {
        $env:PYTHONPATH = $sourcePath
        $env:PYTHONUTF8 = '1'
        & $python -m dev_tools.cli @CliArguments
        $exitCode = $LASTEXITCODE
    }
    finally {
        $env:PYTHONPATH = $previousPythonPath
        $env:PYTHONUTF8 = $previousPythonUtf8
    }
    exit $exitCode
}

function Invoke-CliCommand {
    Assert-Mise
    $action = if ($Arguments.Count -gt 0) { $Arguments[0] } else { 'status' }
    if ($action -notin @('status', 'install', 'outdated', 'upgrade')) {
        throw "未知 CLI 命令：$action"
    }
    $previousConfig = $env:MISE_GLOBAL_CONFIG_FILE
    try {
        $env:MISE_GLOBAL_CONFIG_FILE = $cliConfigPath
        switch ($action) {
            'status' { & mise ls --current }
            'install' { & mise --yes install }
            'outdated' { & mise outdated }
            'upgrade' { & mise --yes upgrade }
        }
        exit $LASTEXITCODE
    }
    finally {
        $env:MISE_GLOBAL_CONFIG_FILE = $previousConfig
    }
}

function Invoke-WindowsCheck {
    param([ValidateSet('status', 'doctor')][string] $Action)

    Write-Host "`n== Windows ==" -ForegroundColor Cyan
    Assert-Mise
    if ($Action -eq 'status') {
        & mise --version
    } else {
        & mise doctor
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Windows mise $Action failed with exit code $LASTEXITCODE."
    }
}

function Invoke-WslCheck {
    param([ValidateSet('status', 'doctor')][string] $Action)

    Write-Host "`n== WSL: $Distro ==" -ForegroundColor Cyan
    if ($Action -eq 'status') {
        & wsl.exe -d $Distro -- bash -lc 'mise --version && python3 --version'
    } else {
        & wsl.exe -d $Distro -- bash -lc 'mise doctor'
    }
    if ($LASTEXITCODE -ne 0) {
        throw "WSL mise $Action failed with exit code $LASTEXITCODE."
    }
}

if ($Command -in @('help', '-h', '--help')) {
    Show-Help
    exit 0
}
if ($Command -in @('version', '-V', '--version')) {
    Invoke-InternalCli -CliArguments @('--version')
}
if ($Command -eq 'project') {
    $cliArguments = @('project') + $Arguments
    Invoke-InternalCli -CliArguments $cliArguments
}
if ($Command -eq 'cli') {
    Invoke-CliCommand
}
if ($Command -notin @('status', 'doctor')) {
    Write-Error "未知命令：$Command"
    Show-Help
    exit 2
}

Invoke-WindowsCheck -Action $Command
Invoke-WslCheck -Action $Command
