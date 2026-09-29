# Run the MCP runner on this machine (task 4.4). Run from anywhere:  .\scripts\dev-mcp-runner.ps1
# Local-command (stdio) MCP servers run here, never inside the API.
#
# The runner reads only its own variables, never .env: .env holds the
# platform's secrets, and the runner must not. So this script asks the API's
# settings for the shared token (MCP_RUNNER_TOKEN, or one derived from
# APP_KEK when that is empty) and hands the runner just that.
#
# On Windows the sandbox is partial: a clean environment, a private temp
# directory, and wall-clock and idle limits. Memory, CPU and process limits
# need Linux (the mcp-runner container). /healthz on port 8100 lists exactly
# what is applied.
. (Join-Path $PSScriptRoot "_lib.ps1")
Set-Location (Join-Path (Get-RepoRoot) "apps/api")
$py = Get-VenvPython

$token = & $py -c "from app.config import settings; print(settings.mcp_runner_token_effective)"
if (-not $token) { throw "No MCP runner token: set MCP_RUNNER_TOKEN (or APP_KEK) in .env" }
$url = & $py -c "from app.config import settings; print(settings.mcp_runner_url)"
$port = ([uri]$url).Port

$env:MCP_RUNNER_TOKEN = $token
$env:MCP_RUNNER_PORT = "$port"
$env:MCP_RUNNER_HOST = "127.0.0.1"
$env:MCP_RUNNER_NETWORK = "host"
Write-Host "MCP runner on http://127.0.0.1:$port (token from the API's settings, not shown)" -ForegroundColor Cyan
& $py -m app.mcp.runner
