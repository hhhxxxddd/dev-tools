[CmdletBinding()]
param([Parameter(Mandatory)][ValidateSet('check', 'install', 'uninstall')][string] $Action)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'dev-tools-messages.ps1')
$Language = Get-DevToolsStartupLanguage @()
. (Join-Path $PSScriptRoot 'verify-source.ps1')
Assert-DevToolsSource $repoRoot

function Assert-InactiveWindowsWorkers {
    $base = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $env:USERPROFILE 'AppData/Local' }
    $stateRoot = if ($env:DEV_TOOLS_STATE_ROOT) { $env:DEV_TOOLS_STATE_ROOT } else { Join-Path $base 'dev-tools/state' }
    foreach ($identity in @(Get-ChildItem -LiteralPath $stateRoot -Filter '*.json' -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.Directory.Name -eq 'native' -and $_.Name -notlike '*.launch.json' })) {
        $value = Get-Content -LiteralPath $identity.FullName -Raw -Encoding utf8 | ConvertFrom-Json
        $worker = Get-Process -Id ([int]$value.pid) -ErrorAction SilentlyContinue
        if ($worker -and $worker.StartTime.ToUniversalTime().Ticks -eq [long]$value.start_ticks) {
            throw (Get-DevToolsMessage 'Stop Windows projects before updating or uninstalling dev-tools: dev-tools -e win stop {project}' $Language @{project=$identity.Directory.Parent.Name})
        }
    }
}

Assert-InactiveWindowsWorkers
if ($Action -eq 'install') {
    & (Join-Path $PSScriptRoot 'install.ps1') -NoProfile
    # Remove only our marked blocks, so an old checkout function cannot shadow the shim.
    $pattern = '(?ms)^# >>> dev-tools >>>.*?^# <<< dev-tools <<<\r?\n?'
    $profilePaths = @($PROFILE.CurrentUserCurrentHost, $PROFILE.CurrentUserAllHosts)
    $profileDirectory = Split-Path -Parent $PROFILE.CurrentUserAllHosts
    if ((Split-Path -Leaf $profileDirectory) -in @('PowerShell', 'WindowsPowerShell')) {
        $documentDirectory = Split-Path -Parent $profileDirectory
        foreach ($folder in @('PowerShell', 'WindowsPowerShell')) {
            $profilePaths += @(Get-ChildItem -LiteralPath (Join-Path $documentDirectory $folder) -Filter '*.ps1' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
        }
    }
    foreach ($profilePath in $profilePaths | Select-Object -Unique) {
        if (Test-Path -LiteralPath $profilePath) {
            $content = [IO.File]::ReadAllText($profilePath)
            $updated = [regex]::Replace($content, $pattern, '')
            if ($content -ne $updated) { [IO.File]::WriteAllText($profilePath, $updated, [Text.UTF8Encoding]::new($false)) }
        }
    }
    Write-Host (Get-DevToolsMessage 'Scoop entrypoint installed. Open a new terminal if an old dev-tools function is still loaded.' $Language)
}
# Uninstall/update hooks intentionally retain registrations, preferences and native caches.
