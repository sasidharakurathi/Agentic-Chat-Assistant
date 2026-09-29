# Choose an MCP server's tools and approval rules for an assistant's draft.
# A development stand-in for the canvas editor (task 4.10). Examples:
#   .\scripts\mcp-allow.ps1 --assistant "MCP Lab" --server echo --tools echo,environment
#   .\scripts\mcp-allow.ps1 --assistant "MCP Lab" --server echo --rule echo=auto --rule environment=deny
#   .\scripts\mcp-allow.ps1 --assistant "MCP Lab" --show
. (Join-Path $PSScriptRoot "_lib.ps1")
Set-Location (Join-Path (Get-RepoRoot) "apps/api")
& (Get-VenvPython) -m scripts.mcp_allow @args
