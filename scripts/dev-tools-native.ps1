param(
    [Parameter(Mandatory)][ValidateSet('launch', 'query', 'stop')][string]$Action,
    [Parameter(Mandatory)][string]$RequestPath,
    [ValidateSet('zh', 'en')][string]$Language = 'zh'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'dev-tools-messages.ps1')
$request = Get-Content -LiteralPath $RequestPath -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
if ($Action -eq 'launch') {
    $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{
        ShowWindow = [uint16]0
        EnvironmentVariables = [string[]]@($request.environment.Keys | ForEach-Object { "$_=$($request.environment[$_])" })
    }
    $result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = [string]$request.command_line
        CurrentDirectory = [string]$request.cwd
        ProcessStartupInformation = $startup
    }
    if ($result.ReturnValue -ne 0) { throw (Get-DevToolsMessage 'Native launch failed (WMI code {code})' $Language @{code = $result.ReturnValue}) }
    $process = Get-Process -Id ([int]$result.ProcessId)
    @{ pid = $process.Id; start_ticks = $process.StartTime.ToUniversalTime().Ticks } | ConvertTo-Json -Compress
    exit 0
}
function Get-OwnedProcess {
    try {
        $candidate = Get-Process -Id ([int]$request.pid) -ErrorAction Stop
        if (-not $candidate.HasExited -and $candidate.StartTime.ToUniversalTime().Ticks -eq [long]$request.start_ticks) {
            return $candidate
        }
    } catch { }
    return $null
}
$process = Get-OwnedProcess
if ($Action -eq 'stop' -and $process) {
    for ($attempt = 0; $attempt -lt 3 -and $process; $attempt++) {
        $terminationOutput = & taskkill.exe /PID $process.Id /T /F 2>&1
        # taskkill can report a disappearing descendant even after stopping the worker.
        $process = Get-OwnedProcess
        if ($process) { Start-Sleep -Milliseconds 100 }
    }
    if ($process) { throw (Get-DevToolsMessage 'Could not stop native process {pid}: {detail}' $Language @{pid = $process.Id; detail = $terminationOutput}) }
}
@{ active = ($null -ne $process) } | ConvertTo-Json -Compress
