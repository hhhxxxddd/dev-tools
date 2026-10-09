param(
    [string] $Distro = 'Ubuntu',
    [ValidateSet('zh', 'en')][string] $Language = 'zh',
    [Parameter(ValueFromRemainingArguments)][string[]] $Arguments
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'dev-tools-messages.ps1')
$distro = $Distro
function Start-WslKeepAlive {
    $script = Join-Path $PSScriptRoot 'dev-tools-keepalive.sh'
    $linuxScript = & wsl.exe -d $distro --exec wslpath -u $script.Replace('\', '/')
    if ($LASTEXITCODE -ne 0) { throw (Get-DevToolsMessage 'Could not locate WSL keepalive script' $Language) }
    $wrapper = Join-Path $PSScriptRoot 'dev-tools-keepalive.ps1'
    $arguments = '-NoProfile -NonInteractive -File "' + $wrapper + '" -Distro "' + $distro +
        '" -LinuxScript "' + ([string]$linuxScript).Trim() + '"'
    $existing = @(Get-CimInstance Win32_Process -Filter "Name = 'pwsh.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains($arguments) })
    if (-not $existing.Count) {
        # Detach from short-lived terminal/agent process trees, like Windows workers.
        $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
        $result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
            CommandLine = '"' + (Join-Path $PSHOME 'pwsh.exe') + '" ' + $arguments
            ProcessStartupInformation = $startup
        }
        if ($result.ReturnValue -ne 0) { throw (Get-DevToolsMessage 'Could not keep WSL running (WMI code {code})' $Language @{code = $result.ReturnValue}) }
    }
}

if ($Arguments.Count -gt 0 -and $Arguments[0] -in @('start', 'restart', 'prepare') -and $Arguments -notcontains '--dry-run' -and $Arguments -notcontains '--help' -and $Arguments -notcontains '-h') {
    Start-WslKeepAlive
}
function Find-SourceArgument([string[]]$tokens) {
    for ($index = 1; $index -lt $tokens.Count; $index++) {
        if ($tokens[$index] -in @('--name', '--user', '--runtime', '--toolchain')) { $index++; continue }
        if (-not $tokens[$index].StartsWith('-')) { return $index }
    }
    return -1
}
$pathIndex = -1
if ($Arguments.Count -gt 0 -and $Arguments -notcontains '--help' -and $Arguments -notcontains '-h') {
    switch ($Arguments[0]) {
        'register' {
            $pathIndex = Find-SourceArgument $Arguments
            if ($pathIndex -lt 0) { $Arguments += '.'; $pathIndex = $Arguments.Count - 1 }
        }
        'init' {
            $pathIndex = Find-SourceArgument $Arguments
            if ($pathIndex -lt 0) { $Arguments += '.'; $pathIndex = $Arguments.Count - 1 }
        }
        'scan' {
            $pathIndex = Find-SourceArgument $Arguments
            if ($pathIndex -lt 0) { $Arguments += '.'; $pathIndex = $Arguments.Count - 1 }
        }
    }
}
if ($pathIndex -ge 0) {
    if (-not $Arguments[$pathIndex].StartsWith('/')) {
        $nativePath = (Resolve-Path -LiteralPath $Arguments[$pathIndex]).ProviderPath
        $mapped = & wsl.exe -d $Distro --exec wslpath -u $nativePath.Replace('\', '/')
        if ($LASTEXITCODE -ne 0) { throw (Get-DevToolsMessage 'Could not translate project path into WSL' $Language) }
        $Arguments[$pathIndex] = ([string]$mapped).Trim()
    }
}
$control = $Arguments.Count -gt 0 -and $Arguments[0] -in @('register', 'prepare', 'start', 'stop', 'restart', 'sync', 'build', 'rename', 'unregister') -and
    $Arguments -notcontains '--help' -and $Arguments -notcontains '-h' -and $Arguments -notcontains '--dry-run'
if ($control -and $Arguments[0] -eq 'register' -and -not ($Arguments | Where-Object { $_ -eq '--user' -or $_.StartsWith('--user=') })) {
    $nativeUser = & wsl.exe -d $Distro --exec id -un
    if ($LASTEXITCODE -ne 0) { throw (Get-DevToolsMessage 'Could not resolve the WSL runtime user' $Language) }
    $Arguments += @('--user', ([string]$nativeUser).Trim())
}
$nativeCwd = (Get-Location).ProviderPath
$linuxCwd = & wsl.exe -d $Distro --exec wslpath -u $nativeCwd.Replace('\', '/')
if ($LASTEXITCODE -ne 0) { throw (Get-DevToolsMessage 'Could not translate current directory into WSL' $Language) }
if ($control) {
    & wsl.exe -d $Distro -u root --cd ([string]$linuxCwd).Trim() --exec env -u DEV_TOOLS_CONFIG "DEV_TOOLS_TRANSPORT_LANGUAGE=$Language" dev-tools -e wsl @Arguments
} else {
    & wsl.exe -d $Distro --cd ([string]$linuxCwd).Trim() --exec env -u DEV_TOOLS_CONFIG "DEV_TOOLS_TRANSPORT_LANGUAGE=$Language" dev-tools -e wsl @Arguments
}
exit $LASTEXITCODE
