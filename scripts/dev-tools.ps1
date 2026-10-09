# Keep CLI flags raw: advanced PowerShell binding treats -e as an ambiguous
# abbreviation of ErrorAction/ErrorVariable before Python can parse it.
$rawArguments = @($args)
. (Join-Path $PSScriptRoot 'dev-tools-messages.ps1')
$startupLanguage = Get-DevToolsStartupLanguage $rawArguments
$Arguments = @()
$Distro = $null
for ($index = 0; $index -lt $rawArguments.Count; $index++) {
    if ($rawArguments[$index] -eq '-Distro') {
        $index++
        if ($index -eq $rawArguments.Count) { throw (Get-DevToolsMessage '-Distro requires a distribution name' $startupLanguage) }
        $Distro = $rawArguments[$index]
    } elseif ($rawArguments[$index] -like '-Distro=*') {
        $Distro = $rawArguments[$index].Substring(8)
        if (-not $Distro) { throw (Get-DevToolsMessage '-Distro requires a distribution name' $startupLanguage) }
    } else {
        $Arguments += $rawArguments[$index]
    }
}

if (-not $Arguments.Count) { $Arguments = @('help') }

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$sourcePath = Join-Path $repoRoot 'src'
$internalPython = 'python@3.14.8'

function Assert-Mise {
    if (-not (Get-Command mise -ErrorAction SilentlyContinue)) {
        throw (Get-DevToolsMessage 'mise is not available on Windows. Install it with: scoop install mise' $startupLanguage)
    }
}

function Get-InternalPython {
    Assert-Mise
    $pythonRoots = @(& mise --no-config -C $repoRoot where $internalPython 2>$null)
    $miseExitCode = $LASTEXITCODE
    $pythonRoot = "$($pythonRoots | Select-Object -First 1)".Trim()
    $python = if ($pythonRoot) { Join-Path $pythonRoot 'python.exe' } else { $null }
    if ($miseExitCode -ne 0 -or -not $python -or -not (Test-Path -LiteralPath $python)) {
        throw (Get-DevToolsMessage 'dev-tools internal Python is missing. Rerun scripts\install.ps1.' $startupLanguage)
    }
    return $python
}

function Invoke-InternalCli {
    param([Parameter(Mandatory)][string[]] $CliArguments)

    $python = Get-InternalPython
    $previousPythonPath = $env:PYTHONPATH
    $previousPythonUtf8 = $env:PYTHONUTF8
    $previousDistro = $env:DEV_TOOLS_DISTRO
    try {
        $env:PYTHONPATH = $sourcePath
        $env:PYTHONUTF8 = '1'
        if ($Distro) { $env:DEV_TOOLS_DISTRO = $Distro }
        & $python -P -m dev_tools.cli @CliArguments
        $exitCode = $LASTEXITCODE
    }
    finally {
        $env:PYTHONPATH = $previousPythonPath
        $env:PYTHONUTF8 = $previousPythonUtf8
        $env:DEV_TOOLS_DISTRO = $previousDistro
    }
    exit $exitCode
}

Invoke-InternalCli -CliArguments $Arguments
