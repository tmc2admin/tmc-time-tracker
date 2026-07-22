$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created electron_app\.env from .env.example"
}

$npm = Get-Command npm -ErrorAction SilentlyContinue
if ($npm) {
    npm install
    npm start
    exit
}

$pnpmPath = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\pnpm.cmd"
if (Test-Path $pnpmPath) {
    $nodeBin = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin"
    $toolBin = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin"
    $env:PATH = "$nodeBin;$toolBin;$env:PATH"
    & $pnpmPath install --ignore-scripts
    & $pnpmPath start
    exit
}

throw "No npm or bundled pnpm executable found. Install Node.js or run this from Codex with bundled dependencies."
