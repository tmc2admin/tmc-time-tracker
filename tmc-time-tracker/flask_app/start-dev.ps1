$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created flask_app\.env from .env.example"
}

$pythonCandidates = @(
    "python",
    "py",
    "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
)

$python = $null
foreach ($candidate in $pythonCandidates) {
    try {
        $cmd = Get-Command $candidate -ErrorAction Stop
        $python = $cmd.Source
        break
    } catch {}
}

if (-not $python) {
    throw "No Python executable found. Install Python 3 or run this from Codex with the bundled runtime."
}

if (-not (Test-Path ".venv")) {
    & $python -m venv .venv
}

.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\setup_dev_db.py
.\.venv\Scripts\python.exe run.py
