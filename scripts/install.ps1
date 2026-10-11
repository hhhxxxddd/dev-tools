[CmdletBinding()]
param(
    [string] $Distro = 'Ubuntu',
    [switch] $NoProfile
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'dev-tools-messages.ps1')
$language = Get-DevToolsStartupLanguage @()
$hostBinding = Join-Path $repoRoot '.host-python.win'
$entrypoint = Join-Path $repoRoot 'scripts\dev-tools.ps1'
$profilePath = $PROFILE.CurrentUserCurrentHost
$startMarker = '# >>> dev-tools >>>'
$endMarker = '# <<< dev-tools <<<'
$quotedEntrypoint = $entrypoint.Replace("'", "''")
$block = @"
$startMarker
function dev-tools { & '$quotedEntrypoint' @args }
$endMarker
"@

if (Test-Path -LiteralPath $hostBinding) {
    if ((Get-Item -LiteralPath $hostBinding -Force).LinkType -or
        -not (Test-Path -LiteralPath $hostBinding -PathType Leaf)) {
        throw (Get-DevToolsMessage 'Refusing invalid dev-tools Python binding: {path}' $language @{path=$hostBinding})
    }
}
if (Test-Path Env:DEV_TOOLS_PYTHON) {
    $candidate = $env:DEV_TOOLS_PYTHON
} elseif (Test-Path -LiteralPath $hostBinding) {
    $candidate = [IO.File]::ReadAllText($hostBinding).Trim()
} else {
    if (-not (Get-Command mise -ErrorAction SilentlyContinue)) {
        throw (Get-DevToolsMessage 'mise is not available on Windows. Install it with: scoop install mise' $language)
    }
    Write-Host (Get-DevToolsMessage 'Installing the Python runtime used by dev-tools...' $language)
    & mise --no-config --yes -C $repoRoot install python@3.14.8
    if ($LASTEXITCODE -ne 0) {
        throw (Get-DevToolsMessage 'mise failed to install dev-tools Python (exit {code}).' $language @{code=$LASTEXITCODE})
    }
    $pythonRoots = @(& mise --no-config -C $repoRoot where python@3.14.8)
    if ($LASTEXITCODE -ne 0 -or -not $pythonRoots.Count) {
        throw (Get-DevToolsMessage 'dev-tools internal Python is missing. Rerun scripts\install.ps1.' $language)
    }
    $candidate = Join-Path "$($pythonRoots[0])".Trim() 'python.exe'
}
$python = Resolve-DevToolsHostPython $candidate $language
[IO.File]::WriteAllText($hostBinding, "$python`n", [Text.UTF8Encoding]::new($false))

if ($NoProfile) { return }

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

Write-Host (Get-DevToolsMessage 'dev-tools installed for PowerShell: {path}' $language @{path=$entrypoint})
Write-Host (Get-DevToolsMessage 'Restart PowerShell or run: . $PROFILE' $language)
