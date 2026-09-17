# Starts the backend (uvicorn --reload) and dashboard (vite dev), each in
# its own PowerShell window, for local, non-Docker development.
#
#   .\run.ps1
#
# Close either window to stop that process. Assumes
# `python -m venv .venv; .venv\Scripts\Activate.ps1; pip install -r requirements-dev.txt`
# (backend) and `cd frontend; npm install` (dashboard) have already been run
# once -- see the repo README's "Getting started".

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

if (-not (Test-Path "$root\.venv\Scripts\Activate.ps1")) {
    Write-Host "No .venv found -- run: python -m venv .venv; .venv\Scripts\Activate.ps1; pip install -r requirements-dev.txt" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path "$root\frontend\.env.local")) {
    Write-Host "frontend\.env.local missing -- copying frontend\.env.example (defaults to backend on :8000)"
    Copy-Item "$root\frontend\.env.example" "$root\frontend\.env.local"
}

Write-Host "Backend   -> http://localhost:8000 (docs at /docs)"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root'; . .venv\Scripts\Activate.ps1; uvicorn backend.app.main:app --reload"

Write-Host "Dashboard -> http://localhost:5173 (vite picks a free port if taken)"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root\frontend'; npm run dev"
