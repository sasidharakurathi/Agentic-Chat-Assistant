# Apply DB migrations. Run from anywhere:  .\scripts\migrate.ps1
. (Join-Path $PSScriptRoot "_lib.ps1")
Set-Location (Get-RepoRoot)
Invoke-Native (Get-VenvPython) "-m" "alembic" "-c" "apps/api/alembic.ini" "upgrade" "head"
