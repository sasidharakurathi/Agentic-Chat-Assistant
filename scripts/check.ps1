# Lint + typecheck + test everything CI runs. Run from anywhere:  .\scripts\check.ps1
. (Join-Path $PSScriptRoot "_lib.ps1")
$root = Get-RepoRoot
$py = Get-VenvPython
Set-Location $root

Write-Host "==> ruff" -ForegroundColor Cyan
Invoke-Native $py "-m" "ruff" "check" "apps/api"
Invoke-Native $py "-m" "ruff" "format" "--check" "apps/api"

Write-Host "==> mypy (this platform, then Linux as CI and the containers run it)" -ForegroundColor Cyan
Push-Location (Join-Path $root "apps\api")
try {
    Invoke-Native $py "-m" "mypy" "app" "scripts"
    # Windows-only and POSIX-only code both exist (the MCP jail, the event
    # loop check); a Windows-only run never type-checks the Linux branch.
    Invoke-Native $py "-m" "mypy" "app" "scripts" "--platform" "linux"
}
finally { Pop-Location }

Write-Host "==> pytest" -ForegroundColor Cyan
Invoke-Native $py "-m" "pytest" "apps/api" "-q" "-m" "not integration"

Write-Host "==> pytest (integration: needs Docker Postgres up)" -ForegroundColor Cyan
Invoke-Native $py "-m" "pytest" "apps/api" "-q" "-m" "integration"

# Both test processes have exited, so nothing holds their files open any
# more: remove their folders under %TEMP%\assistant-studio-tests (on Windows
# a run can't delete its own SQLite file at exit; see tests/temp_dirs.py).
Push-Location (Join-Path $root "apps\api")
try { Invoke-Native $py "-m" "tests.temp_dirs" }
finally { Pop-Location }

Write-Host "==> alembic check (models vs. migrated Postgres schema)" -ForegroundColor Cyan
# The unit tier runs the same check on SQLite; this is the Postgres side, the
# only place the pgvector HNSW / GIN indexes exist. Assumes the dev database
# is at head (`python -m alembic upgrade head` from apps/api if not).
Push-Location (Join-Path $root "apps\api")
try { Invoke-Native $py "-m" "alembic" "check" }
finally { Pop-Location }

Write-Host "==> shared types (generated from the OpenAPI schema)" -ForegroundColor Cyan
# The pytest snapshot above keeps openapi.json in step with the API; this keeps
# the generated TypeScript in step with openapi.json, then compiles it.
Invoke-Native "npm" "run" "gen:check" "-w" "@assistant-studio/shared"
Invoke-Native "npm" "run" "typecheck" "-w" "@assistant-studio/shared"

Write-Host "==> web typecheck + lint + unit tests" -ForegroundColor Cyan
Invoke-Native "npm" "run" "typecheck" "-w" "web"
Invoke-Native "npm" "run" "lint" "-w" "web"
Invoke-Native "npm" "run" "test" "-w" "web"

Write-Host "==> prettier --check" -ForegroundColor Cyan
Invoke-Native "npm" "run" "format:check"

Write-Host "`nAll green." -ForegroundColor Green
