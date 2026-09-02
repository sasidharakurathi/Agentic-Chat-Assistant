# One-time local setup. Run from anywhere:  .\scripts\setup.ps1
. (Join-Path $PSScriptRoot "_lib.ps1")
$root = Get-RepoRoot
Set-Location $root

Write-Host "==> Python venv + API deps" -ForegroundColor Cyan
if (-not (Test-Path ".venv")) { Invoke-Native "python" "-m" "venv" ".venv" }
Invoke-Native $script:VenvPy "-m" "pip" "install" "-U" "pip"
Invoke-Native $script:VenvPy "-m" "pip" "install" "-e" "apps/api[dev]"

Write-Host "==> JS workspace deps" -ForegroundColor Cyan
Invoke-Native "npm" "install"

if (-not (Test-Path ".env")) {
    Copy-Item .env.example .env
    # Bake real dev secrets so the app does not warn / regenerate on every run.
    $jwt = & $script:VenvPy -c "import secrets;print(secrets.token_urlsafe(48))"
    $kek = & $script:VenvPy -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"
    (Get-Content .env) `
        -replace '^JWT_SECRET=.*', "JWT_SECRET=$jwt" `
        -replace '^APP_KEK=.*', "APP_KEK=$kek" |
        Set-Content .env -Encoding ascii
    Write-Host "created .env with generated JWT_SECRET + APP_KEK" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Done. Next:" -ForegroundColor Green
Write-Host "  docker compose up -d postgres redis minio   (or set DATABASE_URL to sqlite in .env)"
Write-Host "  .\scripts\migrate.ps1"
Write-Host "  .\scripts\seed.ps1"
Write-Host "  .\scripts\dev-api.ps1     (terminal 1)"
Write-Host "  .\scripts\dev-web.ps1     (terminal 2)"
