[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$legacyConfigPath = Join-Path $repoRoot 'config\mise.toml'
$cliConfigPath = Join-Path $repoRoot 'config\windows-cli-tools\mise.toml'
$previousCliConfigPath = Join-Path $repoRoot 'config\windows-cli-tools.toml'
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
if ($current -in @($legacyConfigPath, $previousCliConfigPath, $cliConfigPath)) {
    [Environment]::SetEnvironmentVariable('MISE_GLOBAL_CONFIG_FILE', $null, 'User')
}
$fragmentPath = Join-Path $env:USERPROFILE '.config\mise\conf.d\windows-cli-tools'
if (Test-Path -LiteralPath $fragmentPath) {
    $fragment = Get-Item -LiteralPath $fragmentPath -Force
    if ($fragment.LinkType -eq 'Junction' -and $fragment.Target -eq (Split-Path -Parent $cliConfigPath)) {
        # Delete only the managed junction, never its repository target.
        [IO.Directory]::Delete($fragmentPath)
    }
}
Remove-Item Function:dev-tools -ErrorAction SilentlyContinue
Remove-Item Function:wsl-devctl -ErrorAction SilentlyContinue
Write-Host 'dev-tools PowerShell entrypoint removed. Runtime installations were preserved.'
