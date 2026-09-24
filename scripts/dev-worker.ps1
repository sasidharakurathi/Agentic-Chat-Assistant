# Run the Arq background worker (ingests data sources). Run from anywhere:
#   .\scripts\dev-worker.ps1
. (Join-Path $PSScriptRoot "_lib.ps1")
$root = Get-RepoRoot
Set-Location (Join-Path $root "apps\api")
& (Get-VenvPython) -m arq app.worker.WorkerSettings
