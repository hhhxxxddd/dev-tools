[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$configPath = Join-Path $repoRoot 'config\mise.toml'
$entrypoint = Join-Path $repoRoot 'scripts\dev-tools.ps1'
$profilePath = $PROFILE.CurrentUserCurrentHost
$startMarker = '# >>> dev-tools >>>'
$endMarker = '# <<< dev-tools <<<'
$quotedConfig = $configPath.Replace("'", "''")
$quotedEntrypoint = $entrypoint.Replace("'", "''")
$block = @"
$startMarker
`$env:MISE_GLOBAL_CONFIG_FILE = '$quotedConfig'
function dev-tools { & '$quotedEntrypoint' @args }
$endMarker
"@

if (-not (Get-Command mise -ErrorAction SilentlyContinue)) {
    throw 'mise is not available. Install it first, for example with: scoop install mise'
}

[Environment]::SetEnvironmentVariable('MISE_GLOBAL_CONFIG_FILE', $configPath, 'User')
$env:MISE_GLOBAL_CONFIG_FILE = $configPath

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
$updated = if ($content) { "$content`r`n`r`n$block`r`n" } else { "$block`r`n" }
[IO.File]::WriteAllText($profilePath, $updated, [Text.UTF8Encoding]::new($false))

Write-Host "dev-tools installed for PowerShell: $entrypoint"
Write-Host "Restart PowerShell or run: . `$PROFILE"
