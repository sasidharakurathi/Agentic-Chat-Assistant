# Lint + typecheck + test everything CI runs. Run from anywhere:  .\scripts\check.ps1
. (Join-Path $PSScriptRoot "_lib.ps1")
$root = Get-RepoRoot
$py = Get-VenvPython
Set-Location $root

Write-Host "==> ruff" -ForegroundColor Cyan
Invoke-Native $py "-m" "ruff" "check" "apps/api"
Invoke-Native $py "-m" "ruff" "format" "--check" "apps/api"

Write-Host "==> mypy" -ForegroundColor Cyan
Push-Location (Join-Path $root "apps\api")
try { Invoke-Native $py "-m" "mypy" "app" "scripts" }
finally { Pop-Location }

Write-Host "==> pytest" -ForegroundColor Cyan
Invoke-Native $py "-m" "pytest" "apps/api" "-q"

Write-Host "==> web typecheck + lint" -ForegroundColor Cyan
Invoke-Native "npm" "run" "typecheck" "-w" "web"
Invoke-Native "npm" "run" "lint" "-w" "web"

Write-Host "`nAll green." -ForegroundColor Green
