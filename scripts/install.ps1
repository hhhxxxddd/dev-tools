[CmdletBinding()]
param(
    [string] $Distro = 'Ubuntu',
    [switch] $EnableWslDevctlForwarder
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$legacyConfigPath = Join-Path $repoRoot 'config\mise.toml'
$cliConfigPath = Join-Path $repoRoot 'config\windows-cli-tools\mise.toml'
$entrypoint = Join-Path $repoRoot 'scripts\dev-tools.ps1'
$profilePath = $PROFILE.CurrentUserCurrentHost
$startMarker = '# >>> dev-tools >>>'
$endMarker = '# <<< dev-tools <<<'
$quotedEntrypoint = $entrypoint.Replace("'", "''")
$previousCliConfigPath = Join-Path $repoRoot 'config\windows-cli-tools.toml'
$quotedDistro = $Distro.Replace("'", "''")
$forwarder = if ($EnableWslDevctlForwarder) {
    "function wsl-devctl { & wsl.exe -d '$quotedDistro' -- wsl-devctl @args }"
} else {
    ''
}
$block = @"
$startMarker
function dev-tools { & '$quotedEntrypoint' @args }
$forwarder
$endMarker
"@

if (-not (Get-Command mise -ErrorAction SilentlyContinue)) {
    throw 'mise is not available. Install it first, for example with: scoop install mise'
}

# Personal global defaults remain outside the repository. Link the operator
# fragment instead of copying declarations that could drift after an upgrade.
$fragmentPath = Join-Path $env:USERPROFILE '.config\mise\conf.d\windows-cli-tools'
$fragmentTarget = Split-Path -Parent $cliConfigPath
if (Test-Path -LiteralPath $fragmentPath) {
    $fragment = Get-Item -LiteralPath $fragmentPath -Force
    if ($fragment.LinkType -ne 'Junction' -or $fragment.Target -ne $fragmentTarget) {
        throw 'The operator CLI fragment path already exists with a different target.'
    }
} else {
    New-Item -ItemType Directory -Path (Split-Path -Parent $fragmentPath) -Force | Out-Null
    New-Item -ItemType Junction -Path $fragmentPath -Target $fragmentTarget | Out-Null
}
$managedConfigs = @($legacyConfigPath, $previousCliConfigPath, $cliConfigPath)
$userConfig = [Environment]::GetEnvironmentVariable('MISE_GLOBAL_CONFIG_FILE', 'User')
if ($userConfig -in $managedConfigs) {
    [Environment]::SetEnvironmentVariable('MISE_GLOBAL_CONFIG_FILE', $null, 'User')
}
if ($env:MISE_GLOBAL_CONFIG_FILE -in $managedConfigs) {
    Remove-Item Env:MISE_GLOBAL_CONFIG_FILE
}

Write-Host 'Installing the private Python runtime used by dev-tools...'
& mise --yes install python@3.14.8
if ($LASTEXITCODE -ne 0) {
    throw "mise failed to install dev-tools internal Python (exit $LASTEXITCODE)."
}

$profileDirectory = Split-Path -Parent $profilePath
if (-not (Test-Path -LiteralPath $profileDirectory)) {
    New-Item -ItemType Directory -Path $profileDirectory -Force | Out-Null
}
$content = if (Test-Path -LiteralPath $profilePath) {
    Get-Content -LiteralPath $profilePath -Raw
} else {
    ''
}
$pattern = "(?ms)^$([regex]::Escape($startMarker)).*?^$([regex]::Escape($endMarker))\r?\n?"
$content = [regex]::Replace($content, $pattern, '').TrimEnd()
foreach ($managedConfigPath in $managedConfigs) {
    $assignment = "`$env:MISE_GLOBAL_CONFIG_FILE = '$managedConfigPath'"
    $content = $content.Replace("$assignment`r`n", '').Replace("$assignment`n", '')
}
$updated = if ($content) { "$content`r`n`r`n$block`r`n" } else { "$block`r`n" }
[IO.File]::WriteAllText($profilePath, $updated, [Text.UTF8Encoding]::new($false))

Write-Host "dev-tools installed for PowerShell: $entrypoint"
if ($EnableWslDevctlForwarder) {
    Write-Host "wsl-devctl PowerShell forwarder enabled for WSL distro: $Distro"
}
Write-Host "Restart PowerShell or run: . `$PROFILE"
