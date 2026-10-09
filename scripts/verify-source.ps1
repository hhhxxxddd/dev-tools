function Assert-DevToolsSource([string] $Root) {
    $receipt = Join-Path $Root '.release.json'
    if ((Test-Path -LiteralPath $receipt -PathType Leaf) -and -not (Test-Path -LiteralPath (Join-Path $Root '.git'))) {
        $value = Get-Content -LiteralPath $receipt -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
        if ($value.repository -ne 'hhhxxxddd/dev-tools' -or $value.files -isnot [hashtable]) {
            throw 'Refusing an unverified dev-tools package.'
        }
        $boundary = [IO.Path]::GetFullPath($Root).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
        foreach ($name in $value.files.Keys) {
            $target = [IO.Path]::GetFullPath((Join-Path $Root $name))
            if (-not $target.StartsWith($boundary, [StringComparison]::OrdinalIgnoreCase) -or
                -not (Test-Path -LiteralPath $target -PathType Leaf) -or
                (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $value.files[$name]) {
                throw "Controller package checksum failed: $name"
            }
        }
        foreach ($required in @('scripts/install.sh', 'scripts/install-wsl.ps1', 'src/dev_tools/__init__.py')) {
            if (-not $value.files.ContainsKey($required)) { throw 'Incomplete controller package receipt.' }
        }
        return
    }
    $origin = & git -C $Root remote get-url origin 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]$origin -notmatch '^(?:git@github\.com:|https://github\.com/)hhhxxxddd/dev-tools(?:\.git)?$') {
        throw 'Refusing to install an unverified dev-tools checkout.'
    }
}
