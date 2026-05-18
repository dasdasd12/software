<#
.SYNOPSIS
    Health check and session monitor for Bridge Server, Claude Code, and Codex.
.DESCRIPTION
    Checks CLI accessibility, workspace config, active sessions, and environment.
    Designed for CH32H417 Keyboard AI Terminal software workspace.
#>

$ErrorActionPreference = "SilentlyContinue"

# --------------------------------------------------------------------------- #
#  Configuration
# --------------------------------------------------------------------------- #

$ScriptDir     = Split-Path -Parent $MyInvocation.MyCommand.Path
$Workspace     = Split-Path -Parent $ScriptDir
$BridgeHost    = "localhost"
$BridgePort    = 8765
$BridgeLog     = "$Workspace\src\bridge\bridge_server.log"
$SessionDir    = "$Workspace\src\bridge\sessions"

# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #

function Test-Command {
    param([string]$Cmd)
    try { $null = Get-Command $Cmd -ErrorAction Stop; return $true } catch { return $false }
}

function Test-TcpPort {
    param([string]$HostName, [int]$Port)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect($HostName, $Port)
        $client.Close()
        return $true
    } catch { return $false }
}

function Write-Status {
    param([string]$Label, [bool]$Ok, [string]$Detail = "")
    $status = if ($Ok) { "YES" } else { "NO " }
    $color  = if ($Ok) { "Green" } else { "Red" }
    Write-Host "  $Label : " -NoNewline
    Write-Host $status -ForegroundColor $color -NoNewline
    if ($Detail) { Write-Host "  ($Detail)" } else { Write-Host }
}

function Write-Header {
    param([string]$Title)
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host " $Title" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
}

# --------------------------------------------------------------------------- #
#  Main Report
# --------------------------------------------------------------------------- #

Write-Header "AI Tools Monitor for CH32H417 software workspace"

# --- Bridge Server ---
Write-Header "Bridge Server"
$bridgeRunning = Test-TcpPort $BridgeHost $BridgePort
Write-Status "WebSocket listening  " $bridgeRunning "ws://${BridgeHost}:${BridgePort}"
Write-Status "Config file exists   " (Test-Path "$Workspace\src\bridge\config.yaml") "$Workspace\src\bridge\config.yaml"
Write-Status "Log file exists      " (Test-Path $BridgeLog) $BridgeLog

# Session count
$sessionCount = 0
if (Test-Path "$SessionDir\sessions.json") {
    try {
        $sessions = Get-Content "$SessionDir\sessions.json" -Raw | ConvertFrom-Json
        $sessionCount = $sessions.Length
    } catch {}
}
Write-Host "  Active sessions    : $sessionCount"

# --- AI Agents ---
Write-Header "AI Agents"
$claudeAvail = Test-Command "claude"
$codexAvail  = Test-Command "codex"
Write-Status "Claude Code CLI      " $claudeAvail
Write-Status "Codex CLI            " $codexAvail

if ($claudeAvail) {
    $cv = & claude --version 2>$null
    Write-Host "  Claude version     : $cv"
}
if ($codexAvail) {
    $cv = ""
    $cv = & codex --version 2>$null
    if ($LASTEXITCODE -eq 0 -and $cv) {
        Write-Host "  Codex version      : $cv"
    } else {
        Write-Host "  Codex version      : unavailable"
    }
}

# --- Workspace Config ---
Write-Header "Workspace Config"
Write-Status ".claude/settings     " (Test-Path "$Workspace\.claude\settings.local.json")
Write-Status "CLAUDE.md            " (Test-Path "$Workspace\CLAUDE.md")
Write-Status ".codex/config        " (Test-Path "$Workspace\.codex\config.toml")

# --- Environment ---
Write-Header "Environment"
Write-Host "  Node.js            : $(if (Test-Command "node") { (& node --version) } else { "NOT FOUND" })"
Write-Host "  npm                : $(if (Test-Command "npm") { (& npm --version) } else { "NOT FOUND" })"
Write-Host "  Python             : $(if (Test-Command "python") { (& python --version 2>&1) } else { "NOT FOUND" })"
Write-Host "  Git                : $(if (Test-Command "git") { (& git --version) } else { "NOT FOUND" })"

# Check Python packages
$hasWebsockets = $false
if (Test-Command "python") {
    try {
        $null = & python -c "import websockets" 2>$null
        $hasWebsockets = $?
    } catch {}
}
Write-Status "Python websockets    " $hasWebsockets "pip install websockets"

$hasYaml = $false
if (Test-Command "python") {
    try {
        $null = & python -c "import yaml" 2>$null
        $hasYaml = $?
    } catch {}
}
Write-Status "Python PyYAML        " $hasYaml "pip install pyyaml"

# --- Summary ---
Write-Header "Summary"
$allOk = $bridgeRunning -and $claudeAvail -and $codexAvail -and $hasWebsockets -and $hasYaml
Write-Status "Overall ready        " $allOk

if (-not $allOk) {
    Write-Host ""
    Write-Host "Diagnostics:" -ForegroundColor Yellow
    if (-not $bridgeRunning) {
        Write-Host "  - Bridge Server not running. Start with:" -ForegroundColor Red
        Write-Host "    cd src/bridge && python server.py" -ForegroundColor Gray
    }
    if (-not $claudeAvail) {
        Write-Host "  - Claude Code CLI not found. Install via VS Code extension or npm." -ForegroundColor Red
    }
    if (-not $codexAvail) {
        Write-Host "  - Codex CLI not found. Install: npm install -g @openai/codex" -ForegroundColor Red
    }
    if (-not $hasWebsockets) {
        Write-Host "  - Missing Python package: pip install websockets" -ForegroundColor Red
    }
    if (-not $hasYaml) {
        Write-Host "  - Missing Python package: pip install pyyaml" -ForegroundColor Red
    }
}

Write-Host ""
