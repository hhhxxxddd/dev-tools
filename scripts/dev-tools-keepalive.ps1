param([Parameter(Mandatory)][string]$Distro, [Parameter(Mandatory)][string]$LinuxScript)
$ErrorActionPreference = 'Stop'
$directory = Join-Path $env:LOCALAPPDATA 'dev-tools'
[IO.Directory]::CreateDirectory($directory) | Out-Null
$safeName = $Distro -replace '[^a-zA-Z0-9._-]', '_'
# Give wsl.exe valid standard handles even in a detached WMI process.
& wsl.exe -d $Distro --exec bash $LinuxScript *> (Join-Path $directory "keepalive-$safeName.log")
exit $LASTEXITCODE
