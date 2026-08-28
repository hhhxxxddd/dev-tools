[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$configPath = Join-Path $repoRoot 'config\mise.toml'
$profilePath = $PROFILE.CurrentUserCurrentHost
$startMarker = '# >>> dev-tools >>>'
$endMarker = '# <<< dev-tools <<<'

if (Test-Path -LiteralPath $profilePath) {
    $content = Get-Content -LiteralPath $profilePath -Raw
    $pattern = "(?ms)^$([regex]::Escape($startMarker)).*?^$([regex]::Escape($endMarker))\r?\n?"
    $updated = [regex]::Replace($content, $pattern, '')
    [IO.File]::WriteAllText($profilePath, $updated, [Text.UTF8Encoding]::new($false))
}

$current = [Environment]::GetEnvironmentVariable('MISE_GLOBAL_CONFIG_FILE', 'User')
if ($current -eq $configPath) {
    [Environment]::SetEnvironmentVariable('MISE_GLOBAL_CONFIG_FILE', $null, 'User')
}
Remove-Item Function:dev-tools -ErrorAction SilentlyContinue
Write-Host 'dev-tools PowerShell entrypoint removed. Runtime installations were preserved.'
