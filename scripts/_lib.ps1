# Shared helpers for the dev scripts. ASCII only: Windows PowerShell 5.1 reads
# .ps1 as the system ANSI codepage, so non-ASCII bytes break parsing.
#
# We deliberately do NOT set $ErrorActionPreference = "Stop": PS 5.1 turns a
# native command's stderr into error records, which with "Stop" aborts the script
# even on exit code 0 (Alembic and uvicorn log to stderr). Invoke-Native checks
# the real exit code instead.

$script:RepoRoot = Split-Path -Parent $PSScriptRoot
$script:VenvPy = Join-Path $script:RepoRoot ".venv\Scripts\python.exe"

function Get-RepoRoot { $script:RepoRoot }

function Get-VenvPython {
    if (-not (Test-Path $script:VenvPy)) {
        throw "venv not found at $($script:VenvPy). Run .\scripts\setup.ps1 first."
    }
    $script:VenvPy
}

function Invoke-Native {
    param(
        [Parameter(Mandatory)][string]$File,
        [Parameter(ValueFromRemainingArguments)][string[]]$CmdArgs
    )
    & $File @CmdArgs
    if ($LASTEXITCODE -ne 0) {
        throw "command failed (exit $LASTEXITCODE): $File $($CmdArgs -join ' ')"
    }
}
