# Seed a demo org + admin user. Run from anywhere:  .\scripts\seed.ps1
. (Join-Path $PSScriptRoot "_lib.ps1")
$py = Get-VenvPython
Push-Location (Join-Path (Get-RepoRoot) "apps\api")
try { Invoke-Native $py "-m" "scripts.seed" }
finally { Pop-Location }
