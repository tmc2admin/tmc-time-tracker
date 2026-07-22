param(
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$appRoot = Split-Path -Parent $PSScriptRoot
$packageFile = Join-Path $appRoot 'package.json'
$package = Get-Content -LiteralPath $packageFile -Raw | ConvertFrom-Json
$version = [string]$package.version

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    $bundledNode = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
    if (Test-Path -LiteralPath $bundledNode) {
        $env:PATH = "$(Split-Path -Parent $bundledNode);$env:PATH"
    }
}

if ($version -notmatch '^\d+\.\d+\.\d+$') {
    throw "package.json contains an invalid release version: $version"
}

$installerName = "Time-tracker-Setup-$version.exe"
$distDir = Join-Path $appRoot 'dist'
$candidateDir = Join-Path $distDir "candidate-$version"
$buildPrefix = "build-$version-"

if ($SkipBuild) {
    $existingBuild = Get-ChildItem -LiteralPath $distDir -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name.StartsWith($buildPrefix) } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $existingBuild) {
        throw "No existing release build found for version $version."
    }
    $releaseBuildDir = $existingBuild.FullName
} else {
    $buildStamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss')
    $releaseBuildDir = Join-Path $distDir "$buildPrefix$buildStamp"
}

if (-not $SkipBuild) {
    Push-Location $appRoot
    try {
        & (Join-Path $appRoot 'node_modules\.bin\electron-builder.cmd') --win --publish never "--config.directories.output=$releaseBuildDir"
        if ($LASTEXITCODE -ne 0) {
            throw "electron-builder failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
}

$installerPath = Join-Path $releaseBuildDir $installerName
$blockmapPath = "$installerPath.blockmap"
$latestPath = Join-Path $releaseBuildDir 'latest.yml'

foreach ($requiredFile in @($installerPath, $blockmapPath, $latestPath)) {
    if (-not (Test-Path -LiteralPath $requiredFile)) {
        throw "Missing release artifact: $requiredFile"
    }
}

New-Item -ItemType Directory -Path $candidateDir -Force | Out-Null
Copy-Item -LiteralPath $installerPath, $blockmapPath, $latestPath -Destination $candidateDir -Force

$hash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash
$manifest = [ordered]@{
    version = $version
    channel = 'test-files'
    installer = $installerName
    sha256 = $hash
    size_bytes = (Get-Item -LiteralPath $installerPath).Length
    created_utc = (Get-Date).ToUniversalTime().ToString('o')
    test_destination = "https://storagetmc1.blob.core.windows.net/xperttimer/test-files/$installerName"
    production_destination = "https://storagetmc1.blob.core.windows.net/xperttimer/updates/"
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $candidateDir 'release-manifest.json') -Encoding utf8

Write-Host "Release candidate ready: $candidateDir"
Write-Host "Release build: $releaseBuildDir"
Write-Host "Installer: $installerName"
Write-Host "SHA256: $hash"
