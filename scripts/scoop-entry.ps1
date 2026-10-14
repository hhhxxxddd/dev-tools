# Scoop's shim can be invoked from cmd.exe or Windows PowerShell 5.1.
$previousConsoleEncoding = [Console]::OutputEncoding
$previousOutputEncoding = $OutputEncoding
$terminalStyle = Get-Variable PSStyle -ValueOnly -ErrorAction SilentlyContinue
$previousOutputRendering = if ($terminalStyle) { $terminalStyle.OutputRendering }
try {
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    $OutputEncoding = [Console]::OutputEncoding
    if ($terminalStyle -and $env:DEV_TOOLS_TERMINAL -eq '1' -and
        -not (Test-Path Env:NO_COLOR) -and $env:TERM -ne 'dumb') {
        $terminalStyle.OutputRendering = 'Ansi'
    }
    & pwsh.exe -NoProfile -File (Join-Path $PSScriptRoot 'dev-tools.ps1') @args
    $exitCode = $LASTEXITCODE
} finally {
    [Console]::OutputEncoding = $previousConsoleEncoding
    $OutputEncoding = $previousOutputEncoding
    if ($terminalStyle) { $terminalStyle.OutputRendering = $previousOutputRendering }
}
exit $exitCode
