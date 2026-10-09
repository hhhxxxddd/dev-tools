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
