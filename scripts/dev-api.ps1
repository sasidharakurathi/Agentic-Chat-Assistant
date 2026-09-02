# Run the API with autoreload. Run from anywhere:  .\scripts\dev-api.ps1
. (Join-Path $PSScriptRoot "_lib.ps1")
Set-Location (Get-RepoRoot)
& (Get-VenvPython) -m uvicorn app.main:app --app-dir apps/api --reload --host 0.0.0.0 --port 8000
