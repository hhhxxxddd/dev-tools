param(
    [string] $ContentFile,
    [ValidatePattern('^[0-9a-f]{64}$')][string] $ExpectedHash,
    [ValidatePattern('^[0-9a-f]{64}$')][string] $ContentHash
)
$ErrorActionPreference = 'Stop'

function Get-HostsHash([byte[]]$Content) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($algorithm.ComputeHash($Content)).Replace('-', '').ToLowerInvariant() }
    finally { $algorithm.Dispose() }
}

function Initialize-DevToolsHostsNative {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class DevToolsHostsNative {
    [StructLayout(LayoutKind.Sequential)] struct Luid { public uint Low; public int High; }
    [StructLayout(LayoutKind.Sequential)] struct TokenPrivileges { public uint Count; public Luid Luid; public uint Attributes; }
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport("advapi32.dll", SetLastError=true)] static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool LookupPrivilegeValue(string system, string name, out Luid luid);
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport("advapi32.dll", SetLastError=true)] static extern bool AdjustTokenPrivileges(IntPtr token, bool disableAll, ref TokenPrivileges privileges, uint size, IntPtr previous, IntPtr length);
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport("dnsapi.dll")] public static extern bool DnsFlushResolverCache();
    public static void EnableOwnerRestore() {
        IntPtr token;
        if (!OpenProcessToken(GetCurrentProcess(), 0x28, out token)) throw new Win32Exception();
        try {
            Luid luid;
            if (!LookupPrivilegeValue(null, "SeRestorePrivilege", out luid)) throw new Win32Exception();
            var privileges = new TokenPrivileges { Count=1, Luid=luid, Attributes=2 };
            if (!AdjustTokenPrivileges(token, false, ref privileges, 0, IntPtr.Zero, IntPtr.Zero) || Marshal.GetLastWin32Error()!=0) throw new Win32Exception();
        } finally { CloseHandle(token); }
    }
}
'@
}

function Set-DevToolsHostsContent([string]$Target, [byte[]]$Content, [string]$Expected) {
    $current = [IO.File]::ReadAllBytes($Target)
    if ((Get-HostsHash $current) -ne $Expected) { throw 'hosts_changed' }
    if ((Get-HostsHash $current) -eq (Get-HostsHash $Content)) { return }
    $temporary = Join-Path ([IO.Path]::GetDirectoryName($Target)) ('.dev-tools-hosts-' + [Guid]::NewGuid().ToString('N'))
    try {
        $acl = Get-Acl -LiteralPath $Target
        $stream = [IO.FileStream]::new($temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try { $stream.Write($Content, 0, $Content.Length); $stream.Flush($true) } finally { $stream.Dispose() }
        Set-Acl -LiteralPath $temporary -AclObject $acl
        # Recheck after staging; Replace preserves the destination's Windows ACL.
        if ((Get-HostsHash ([IO.File]::ReadAllBytes($Target))) -ne $Expected) { throw 'hosts_changed' }
        [IO.File]::Replace($temporary, $Target, [NullString]::Value)
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        if (-not $ContentFile -or -not $ExpectedHash -or -not $ContentHash) { throw 'hosts_invalid_request' }
        # The elevated entrypoint has no destination argument: it can only write Windows hosts.
        $windowsDirectory = [Environment]::GetFolderPath([Environment+SpecialFolder]::Windows)
        $target = Join-Path $windowsDirectory 'System32/drivers/etc/hosts'
        if ((Get-Item -LiteralPath $target).LinkType) { throw 'hosts_invalid_target' }
        $contentItem = Get-Item -LiteralPath $ContentFile
        if ($contentItem.PSIsContainer -or $contentItem.LinkType -or $contentItem.Length -gt 10MB) { throw 'hosts_invalid_request' }
        $content = [IO.File]::ReadAllBytes($ContentFile)
        if ($content.Length -gt 10MB -or (Get-HostsHash $content) -ne $ContentHash) { throw 'hosts_invalid_request' }
        $current = [IO.File]::ReadAllBytes($target)
        if ((Get-HostsHash $current) -ne $ExpectedHash) { throw 'hosts_changed' }
        if ((Get-HostsHash $current) -eq $ContentHash) { exit 0 }
        $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
        if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
            $arguments = '-NoProfile -NonInteractive -File "' + $PSCommandPath + '" -ContentFile "' +
                $ContentFile + '" -ExpectedHash ' + $ExpectedHash + ' -ContentHash ' + $ContentHash
            try {
                $child = Start-Process -FilePath (Join-Path $PSHOME 'pwsh.exe') -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru
                exit $child.ExitCode
            } catch {
                if ($_.Exception.NativeErrorCode -eq 1223 -or $_.Exception.InnerException.NativeErrorCode -eq 1223) { exit 3 }
                throw
            }
        }
        # Preserve SYSTEM/TrustedInstaller ownership on the replacement as well as its DACL.
        Initialize-DevToolsHostsNative
        [DevToolsHostsNative]::EnableOwnerRestore()
        # Serialize elevated writers from different user sessions without trusting user lock files.
        $mutex = [Threading.Mutex]::new($false, 'Global\dev-tools-windows-hosts')
        $locked = $false
        try {
            try { $locked = $mutex.WaitOne(15000) } catch [Threading.AbandonedMutexException] { $locked = $true }
            if (-not $locked) { throw 'hosts_busy' }
            Set-DevToolsHostsContent $target $content $ExpectedHash
            try {
                if (-not [DevToolsHostsNative]::DnsFlushResolverCache()) { exit 4 }
            } catch { exit 4 }
        } finally {
            if ($locked) { $mutex.ReleaseMutex() }
            $mutex.Dispose()
        }
        exit 0
    } catch {
        if ($_.Exception.Message -eq 'hosts_changed') { exit 2 }
        [Console]::Error.WriteLine('hosts_write_failed')
        exit 1
    }
}
