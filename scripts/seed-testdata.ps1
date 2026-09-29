# Test data for the manual walkthrough (docs/MANUAL_TESTING.md). Run from anywhere:
#   .\scripts\seed-testdata.ps1              documents + SQLite + Postgres/MySQL/Mongo
#   .\scripts\seed-testdata.ps1 -DocsOnly    documents + SQLite only
# Safe to re-run. Engines that are not up are skipped with a note.
param([switch]$DocsOnly)

. (Join-Path $PSScriptRoot "_lib.ps1")
$py = Get-VenvPython
Push-Location (Join-Path (Get-RepoRoot) "apps\api")
try {
    if ($DocsOnly) { Invoke-Native $py "-m" "scripts.seed_testdata" "--docs-only" }
    else { Invoke-Native $py "-m" "scripts.seed_testdata" }
}
finally { Pop-Location }
