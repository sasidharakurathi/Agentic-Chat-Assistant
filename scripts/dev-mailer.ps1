# Run the mail queue's worker (Phase 7b.5). Only needed with SMTP_HOST set:
# without it, development mail is written to the API's log at once.
#   .\scripts\dev-mailer.ps1
. (Join-Path $PSScriptRoot "_lib.ps1")
$root = Get-RepoRoot
Set-Location (Join-Path $root "apps\api")
& (Get-VenvPython) -m arq app.worker.MailWorkerSettings
