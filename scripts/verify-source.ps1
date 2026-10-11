function Assert-DevToolsSource([string] $Root) {
    $receipt = Join-Path $Root '.release.json'
    if ((Test-Path -LiteralPath $receipt -PathType Leaf) -and -not (Test-Path -LiteralPath (Join-Path $Root '.git'))) {
        $value = Get-Content -LiteralPath $receipt -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
        if ($value.repository -ne 'hhhxxxddd/dev-tools' -or $value.files -isnot [hashtable] -or
            ($value.schema_version -isnot [int] -and $value.schema_version -isnot [long]) -or $value.schema_version -ne 1) {
            throw 'Refusing an unverified dev-tools package.'
        }
        $boundary = [IO.Path]::GetFullPath($Root).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
        $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        foreach ($name in $value.files.Keys) {
            if ($name -match '(^/|\\|:|(?:^|/)\.\.?(?:/|$)|//)' -or
                -not $seen.Add($name) -or $value.files[$name] -cnotmatch '^[0-9a-f]{64}$') {
                throw 'Unsafe controller package receipt.'
            }
            $target = [IO.Path]::GetFullPath((Join-Path $Root $name))
            if (-not $target.StartsWith($boundary, [StringComparison]::OrdinalIgnoreCase) -or
                -not (Test-Path -LiteralPath $target -PathType Leaf) -or
                (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $value.files[$name]) {
                throw "Controller package checksum failed: $name"
            }
            $component = $Root
            foreach ($part in $name.Split('/')) {
                $component = Join-Path $component $part
                if ((Get-Item -LiteralPath $component -Force).LinkType) { throw 'Symbolic link in controller package.' }
            }
        }
        foreach ($required in @('scripts/install.sh', 'scripts/install-wsl.ps1', 'scripts/install.ps1', 'src/dev_tools/cli.py', 'src/dev_tools/__init__.py', 'pyproject.toml')) {
            if (-not $value.files.ContainsKey($required)) { throw 'Incomplete controller package receipt.' }
        }
        $moduleVersion = [regex]::Match([IO.File]::ReadAllText((Join-Path $Root 'src/dev_tools/__init__.py')), '(?m)^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"\r?$').Groups[1].Value
        $projectVersion = [regex]::Match([IO.File]::ReadAllText((Join-Path $Root 'pyproject.toml')), '(?m)^version = "([0-9]+\.[0-9]+\.[0-9]+)"\r?$').Groups[1].Value
        if (-not $moduleVersion -or $value.version -ne $moduleVersion -or $projectVersion -ne $moduleVersion) { throw 'Controller package version mismatch.' }
        foreach ($directory in @('src/dev_tools', 'scripts', 'config', 'docs', 'examples', 'systemd')) {
            $folder = Join-Path $Root $directory
            if (-not (Test-Path -LiteralPath $folder)) { continue }
            foreach ($item in Get-ChildItem -LiteralPath $folder -Recurse -Force) {
                if ($item.FullName -match '[\\/]__pycache__[\\/]' -or $item.Extension -eq '.pyc') { continue }
                $relative = [IO.Path]::GetRelativePath($Root, $item.FullName).Replace('\', '/')
                if ($item.LinkType -or (-not $item.PSIsContainer -and -not $value.files.ContainsKey($relative))) { throw 'Unlisted file in controller package.' }
            }
        }
        return
    }
    $origin = & git -C $Root remote get-url origin 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]$origin -notmatch '^(?:git@github\.com:|https://github\.com/)hhhxxxddd/dev-tools(?:\.git)?$') {
        throw 'Refusing to install an unverified dev-tools checkout.'
    }
}
