[CmdletBinding()]
param([string] $Distro = 'Ubuntu', [ValidateSet('zh', 'en')][string] $Language, [ValidateNotNullOrEmpty()][string] $Python, [switch] $Yes, [switch] $Recover)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'dev-tools-messages.ps1')
if (-not $Language) { $Language = Get-DevToolsStartupLanguage @() }
. (Join-Path $PSScriptRoot 'verify-source.ps1')
Assert-DevToolsSource $repoRoot

function Invoke-Native {
    param([string] $FilePath, [string[]] $Arguments)
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) { throw (Get-DevToolsMessage '{command} failed with exit code {code}' $Language @{command=$FilePath; code=$LASTEXITCODE}) }
}

function Invoke-WslRootShell([string] $Script) {
    # Carry shell source as base64, rather than interpolating paths or distro names.
    $scopedScript = ('unset DEV_TOOLS_CONFIG; export SUDO_USER=$(printf %s {0} | base64 -d)' -f $script:invokingUserPayload) + "`n" + $Script
    $payload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($scopedScript.Replace("`r`n", "`n")))
    Invoke-Native -FilePath wsl.exe -Arguments @('-d', $Distro, '-u', 'root', '--', 'bash', '-c', "bash -e <(printf '%s' '$payload' | base64 -d)")
}

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { throw (Get-DevToolsMessage 'WSL is unavailable. Install WSL first.' $Language) }
$distros = @(& wsl.exe --list --quiet) | ForEach-Object { ("$_" -replace "`0", '').Trim() }
if ($LASTEXITCODE -ne 0 -or $Distro -notin $distros) { throw (Get-DevToolsMessage 'WSL distro not found: {distro}' $Language @{distro=$Distro}) }
$invokingUser = (& wsl.exe -d $Distro -- id -un | Select-Object -First 1)
if ($LASTEXITCODE -ne 0 -or -not $invokingUser) { throw (Get-DevToolsMessage 'WSL is unavailable. Install WSL first.' $Language) }
$script:invokingUserPayload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(([string]$invokingUser).Trim()))
if ($Recover) {
    $recoverySource = & wsl.exe -d $Distro -- wslpath -a ($repoRoot -replace '\\', '/')
    if ($LASTEXITCODE -ne 0 -or -not $recoverySource) { throw (Get-DevToolsMessage 'Cannot map controller source into WSL: {path}' $Language @{path=$repoRoot}) }
    $recoveryPayload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($recoverySource | Select-Object -First 1)))
    Invoke-WslRootShell ('source_root=$(printf %s {0} | base64 -d); install_root=${{DEV_TOOLS_INSTALL_ROOT:-/opt/dev-tools}}; host=$(cat "$install_root/.host-python"); PYTHONPATH="$source_root/src" DEV_TOOLS_TRANSPORT_LANGUAGE={1} "$host" -P -m dev_tools.cli self recover -e wsl' -f $recoveryPayload, $Language)
    return
}

# Only an explicit target-WSL path crosses the platform boundary.
$pythonSetup = 'unset DEV_TOOLS_PYTHON' + "`n"
if ($Python) {
    $pythonPayload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Python))
    $pythonSetup = ('export DEV_TOOLS_PYTHON=$(printf %s {0} | base64 -d)' -f $pythonPayload) + "`n"
}

# Reviewed Debian/Ubuntu package sources; no curl-pipe-shell bootstrap.
$prerequisites = @'
set -euo pipefail
needs_mise=0
if [[ -z ${DEV_TOOLS_PYTHON:-} && ! -f /opt/dev-tools/.host-python ]] && ! command -v mise >/dev/null 2>&1; then
  needs_mise=1
fi
if [[ $needs_mise -eq 1 ]] || ! command -v rsync >/dev/null 2>&1; then
  command -v apt-get >/dev/null 2>&1 || { printf 'Install mise and rsync in this distro first.\n' >&2; exit 1; }
  if [[ $needs_mise -eq 1 ]]; then
    if ! command -v extrepo >/dev/null 2>&1; then
      apt-get update
      env DEBIAN_FRONTEND=noninteractive apt-get install -y extrepo
    fi
    extrepo enable mise
    apt-get update
    env DEBIAN_FRONTEND=noninteractive apt-get install -y mise
  fi
  if ! command -v rsync >/dev/null 2>&1; then
    apt-get update
    env DEBIAN_FRONTEND=noninteractive apt-get install -y rsync
  fi
fi
'@
Invoke-WslRootShell ($pythonSetup + $prerequisites)
$mapped = & wsl.exe -d $Distro -- wslpath -a ($repoRoot -replace '\\', '/')
if ($LASTEXITCODE -ne 0 -or -not $mapped) { throw (Get-DevToolsMessage 'Cannot map controller source into WSL: {path}' $Language @{path=$repoRoot}) }
$installer = "$($mapped | Select-Object -First 1)/scripts/install.sh"
$installerPayload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($installer))
$yesArgument = if ($Yes) { '--yes' } else { '' }
Invoke-WslRootShell ($pythonSetup + ('installer=$(printf %s {0} | base64 -d)
source_root=$(dirname "$(dirname "$installer")")
install_root=${{DEV_TOOLS_INSTALL_ROOT:-/opt/dev-tools}}
host=$(cat "$install_root/.host-python" 2>/dev/null || true)
if [[ -x "$host" ]]; then
  PYTHONPATH="$source_root/src" DEV_TOOLS_TRANSPORT_LANGUAGE={1} "$host" -P -m dev_tools.controller_update "$source_root" {2}
else
  DEV_TOOLS_TRANSPORT_LANGUAGE={1} bash "$installer"
fi' -f $installerPayload, $Language, $yesArgument))
$windowsSourcePayload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($repoRoot))
Invoke-WslRootShell ('target=${{DEV_TOOLS_INSTALL_ROOT:-/opt/dev-tools}}/.windows-source; test ! -L "$target"; printf %s {0} | base64 -d > "$target"; chmod 0644 "$target"' -f $windowsSourcePayload)
