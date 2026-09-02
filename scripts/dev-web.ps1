# Run the Next.js dev server. Run from anywhere:  .\scripts\dev-web.ps1
. (Join-Path $PSScriptRoot "_lib.ps1")
Set-Location (Get-RepoRoot)
npm run dev -w web
