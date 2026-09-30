[CmdletBinding()]
param(
    [Alias('h')]
    [switch] $Help,
    [Parameter(ValueFromRemainingArguments)]
    [string[]] $Arguments
)

$forwardArgs = @('sysinfo')
if ($Help) { $forwardArgs += '--help' }
if ($Arguments) { $forwardArgs += $Arguments }
& (Join-Path $PSScriptRoot 'dev-tools.ps1') @forwardArgs
exit $LASTEXITCODE
