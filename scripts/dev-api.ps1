# Run the API, restarting it whenever its code changes. Run from anywhere:  .\scripts\dev-api.ps1
# Not `uvicorn --reload`: on Windows its reloader cannot run the real agent
# driver (the Claude Agent SDK starts a subprocess), and forcing a different
# event loop breaks after the first reload. See apps/api/app/devserver.py.
. (Join-Path $PSScriptRoot "_lib.ps1")
Set-Location (Join-Path (Get-RepoRoot) "apps/api")
& (Get-VenvPython) -m watchfiles --filter python --target-type function app.devserver.main app
