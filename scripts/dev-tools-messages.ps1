# Startup diagnostics must also work before the controller's Python is available.
# Read only the documented root language assignment; Python validates the full TOML.
function Get-DevToolsStartupLanguage([string[]] $Tokens) {
    if ($env:DEV_TOOLS_TRANSPORT_LANGUAGE -in @('zh', 'en')) { return $env:DEV_TOOLS_TRANSPORT_LANGUAGE }
    $base = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $env:USERPROFILE 'AppData/Local' }
    $configFile = if ($env:DEV_TOOLS_CONFIG) { $env:DEV_TOOLS_CONFIG } else { Join-Path $base 'dev-tools/config.toml' }
    for ($index = 0; $index -lt $Tokens.Count; $index++) {
        if ($Tokens[$index] -eq '--') { break }
        if ($Tokens[$index] -eq '--config' -and $index + 1 -lt $Tokens.Count) {
            $index++; $configFile = $Tokens[$index]
        } elseif ($Tokens[$index].StartsWith('--config=')) {
            $configFile = $Tokens[$index].Substring(9)
        }
    }
    try {
        foreach ($line in [IO.File]::ReadLines($configFile)) {
            if ($line -match '^\s*\[') { break }
            if ($line -match '^\s*language\s*=\s*["''](zh|en)["'']\s*(#.*)?$') { return $Matches[1] }
        }
    } catch { }
    return 'zh'
}

function Get-DevToolsMessage([string] $Source, [string] $Language = 'zh', [hashtable] $Parameters = @{}) {
    $text = $Source
    if ($Language -eq 'zh') {
        try {
            $catalogPath = Join-Path $PSScriptRoot '../src/dev_tools/locales/zh.json'
            $catalog = Get-Content -LiteralPath $catalogPath -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
            if ($catalog.ContainsKey($Source)) { $text = $catalog[$Source] }
        } catch { $text = 'dev-tools 无法启动，请检查安装。' }
    }
    foreach ($key in $Parameters.Keys) { $text = $text.Replace('{' + $key + '}', [string]$Parameters[$key]) }
    return $text
}

# A host binding is installation state, not a project runtime declaration.
# Isolated Python startup ignores project files, user site packages and PYTHONPATH.
function Get-DevToolsHostPythonPath([string] $Candidate, [string] $Language = 'zh') {
    if (-not $Candidate -or -not [IO.Path]::IsPathFullyQualified($Candidate) -or
        -not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
        throw (Get-DevToolsMessage 'dev-tools Python must be an existing absolute Windows path: {path}' $Language @{path=$Candidate})
    }
    return $Candidate
}

function Resolve-DevToolsHostPython([string] $Candidate, [string] $Language = 'zh') {
    $Candidate = Get-DevToolsHostPythonPath $Candidate $Language
    $probe = 'import json, os, ssl, sys, tomllib; print(json.dumps({"executable": os.path.realpath(sys.executable), "version": list(sys.version_info[:3]), "platform": sys.platform}))'
    $previousNativeErrorPreference = $PSNativeCommandUseErrorActionPreference
    try {
        $PSNativeCommandUseErrorActionPreference = $false
        $output = @(& $Candidate -I -S -c $probe 2>&1)
        $probeExitCode = $LASTEXITCODE
    } catch {
        throw (Get-DevToolsMessage 'Cannot validate dev-tools Python: {path}' $Language @{path=$Candidate})
    } finally {
        $PSNativeCommandUseErrorActionPreference = $previousNativeErrorPreference
    }
    try {
        if ($probeExitCode -ne 0) { throw 'Python validation failed' }
        $identity = ($output -join "`n") | ConvertFrom-Json
        $version = [version]($identity.version -join '.')
        if ($identity.platform -ne 'win32' -or $version -lt [version]'3.14.8' -or
            $version -ge [version]'3.15' -or
            -not [IO.Path]::IsPathFullyQualified($identity.executable) -or
            -not (Test-Path -LiteralPath $identity.executable -PathType Leaf)) {
            throw 'Unsupported Python host'
        }
    } catch {
        throw (Get-DevToolsMessage 'dev-tools requires native Windows Python 3.14.8 or newer in the 3.14 series, with ssl and tomllib: {path}' $Language @{path=$Candidate})
    }
    return [string]$identity.executable
}
