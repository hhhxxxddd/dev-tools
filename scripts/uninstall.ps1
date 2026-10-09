[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$profilePath = $PROFILE.CurrentUserCurrentHost
$startMarker = '# >>> dev-tools >>>'
$endMarker = '# <<< dev-tools <<<'

if (Test-Path -LiteralPath $profilePath) {
    $content = Get-Content -LiteralPath $profilePath -Raw
    $pattern = "(?ms)^$([regex]::Escape($startMarker)).*?^$([regex]::Escape($endMarker))\r?\n?"
    $updated = [regex]::Replace($content, $pattern, '')
    [IO.File]::WriteAllText($profilePath, $updated, [Text.UTF8Encoding]::new($false))
}

Remove-Item Function:dev-tools -ErrorAction SilentlyContinue
Write-Host 'dev-tools PowerShell entrypoint removed. Runtime installations were preserved.'
