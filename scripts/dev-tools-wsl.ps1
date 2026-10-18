[CmdletBinding(PositionalBinding=$false)]
param(
    [string] $Distro = 'Ubuntu',
    [ValidateSet('zh', 'en')][string] $Language = 'zh',
    [string] $ArgumentsPayload,
    [Parameter(ValueFromRemainingArguments)][string[]] $Arguments
)
$ErrorActionPreference = 'Stop'
$previousConsoleEncoding = [Console]::OutputEncoding
$previousOutputEncoding = $OutputEncoding
$previousWslUtf8 = $env:WSL_UTF8
$terminalStyle = Get-Variable PSStyle -ValueOnly -ErrorAction SilentlyContinue
$previousOutputRendering = if ($terminalStyle) { $terminalStyle.OutputRendering }
try {
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    $OutputEncoding = [Console]::OutputEncoding
    # WSL's own diagnostics otherwise use UTF-16 alongside Linux UTF-8 output.
    $env:WSL_UTF8 = '1'
    if ($terminalStyle -and $env:DEV_TOOLS_TERMINAL -eq '1' -and
        -not (Test-Path Env:NO_COLOR) -and $env:TERM -ne 'dumb') {
        $terminalStyle.OutputRendering = 'Ansi'
    }
    . (Join-Path $PSScriptRoot 'dev-tools-messages.ps1')
    if ($ArgumentsPayload) {
        if ($Arguments.Count) { throw (Get-DevToolsMessage 'Invalid WSL argument payload' $Language) }
        try {
            $decoded = ConvertFrom-Json -InputObject ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($ArgumentsPayload))) -NoEnumerate
            if ($decoded -isnot [Array] -or @($decoded | Where-Object { $_ -isnot [string] }).Count) { throw 'invalid_arguments' }
            $Arguments = [string[]]$decoded
        } catch { throw (Get-DevToolsMessage 'Invalid WSL argument payload' $Language) }
    }
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

    if ($Arguments.Count -gt 0 -and $Arguments[0] -in @('start', 'restart', 'prepare') -and $Arguments -notcontains '--help' -and $Arguments -notcontains '-h') {
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
    if ($Arguments.Count -gt 0 -and $Arguments[0] -eq 'report') {
        for ($index = 1; $index -lt $Arguments.Count; $index++) {
            if ($Arguments[$index] -eq '--') { break }
            $inline = $Arguments[$index].StartsWith('--output=')
            if ($Arguments[$index] -ne '--output' -and -not $inline) { continue }
            if ($inline) { $value = $Arguments[$index].Substring(9) }
            else { $index++; $value = $Arguments[$index] }
            if (-not $value.StartsWith('/')) {
                $nativePath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($value)
                $mapped = & wsl.exe -d $Distro --exec wslpath -u $nativePath.Replace('\', '/')
                if ($LASTEXITCODE -ne 0) { throw (Get-DevToolsMessage 'Could not translate report output path into WSL' $Language) }
                $value = ([string]$mapped).Trim()
            }
            $Arguments[$index] = if ($inline) { '--output=' + $value } else { $value }
        }
    }
    $controllerControl = $Arguments.Count -gt 0 -and ($Arguments[0] -eq 'upgrade' -or
        ($Arguments[0] -eq 'self' -and $Arguments.Count -gt 1 -and $Arguments[1] -in @('update', 'upgrade', 'recover')))
    $control = $Arguments.Count -gt 0 -and ($controllerControl -or $Arguments[0] -in @('register', 'prepare', 'start', 'stop', 'restart', 'build', 'rename', 'unregister')) -and
        $Arguments -notcontains '--help' -and $Arguments -notcontains '-h'
    $identity = @()
    if ($control -and ($controllerControl -or ($Arguments[0] -eq 'register' -and -not ($Arguments | Where-Object { $_ -eq '--user' -or $_.StartsWith('--user=') })))) {
        $nativeUser = & wsl.exe -d $Distro --exec id -un
        if ($LASTEXITCODE -ne 0) { throw (Get-DevToolsMessage 'Could not resolve the WSL runtime user' $Language) }
        if ($controllerControl) { $identity = @('SUDO_USER=' + ([string]$nativeUser).Trim()) }
        else { $Arguments += @('--user', ([string]$nativeUser).Trim()) }
    }
    $nativeCwd = (Get-Location).ProviderPath
    $colorEnvironment = @()
    if ((Test-Path Env:NO_COLOR) -or $env:TERM -eq 'dumb') { $colorEnvironment = @('NO_COLOR=1') }
    # --cd accepts absolute Windows paths and translates them in the target distro.
    if ($control) {
        & wsl.exe -d $Distro -u root --cd $nativeCwd --exec env -u DEV_TOOLS_CONFIG "DEV_TOOLS_TRANSPORT_LANGUAGE=$Language" "DEV_TOOLS_TERMINAL=$env:DEV_TOOLS_TERMINAL" @colorEnvironment @identity dev-tools -e wsl @Arguments
    } else {
        & wsl.exe -d $Distro --cd $nativeCwd --exec env -u DEV_TOOLS_CONFIG "DEV_TOOLS_TRANSPORT_LANGUAGE=$Language" "DEV_TOOLS_TERMINAL=$env:DEV_TOOLS_TERMINAL" @colorEnvironment dev-tools -e wsl @Arguments
    }
    $exitCode = $LASTEXITCODE
} finally {
    [Console]::OutputEncoding = $previousConsoleEncoding
    $OutputEncoding = $previousOutputEncoding
    $env:WSL_UTF8 = $previousWslUtf8
    if ($terminalStyle) { $terminalStyle.OutputRendering = $previousOutputRendering }
}
exit $exitCode
