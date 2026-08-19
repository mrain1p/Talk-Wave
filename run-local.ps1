# Starts the whole call-in stack locally in three windows.
#   .\run-local.ps1          - start everything
#   .\run-local.ps1 -Stop    - kill everything
#
# No Docker needed: livekit-server ships a native Windows binary (bin\), and
# the worker + token server run in the venv.

param([switch]$Stop)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if ($Stop) {
    Get-Process livekit-server -ErrorAction SilentlyContinue | Stop-Process -Force
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match 'main\.py|token_server\.py' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Write-Host "stopped." -ForegroundColor Yellow
    return
}

if (-not (Test-Path $python)) { throw "venv missing - run: python -m venv .venv" }
if (-not (Test-Path (Join-Path $root ".env"))) { throw ".env missing - copy .env.example to .env" }

# 1. LiveKit media server. Its output is redirected to data\logs so WebRTC
# problems can be reviewed after the fact (the python processes write their
# own files there via log_setup.py).
$logDir = Join-Path $root "data\logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
# The config path MUST be quoted by hand: Start-Process joins -ArgumentList
# with spaces and does not quote, so "...\Talk Wave\livekit.yaml" otherwise
# arrives as two broken arguments and the server exits at startup.
Start-Process -FilePath (Join-Path $root "bin\livekit-server.exe") `
    -ArgumentList "--config", ('"{0}"' -f (Join-Path $root "livekit.yaml")) `
    -WorkingDirectory $root `
    -RedirectStandardOutput (Join-Path $logDir "livekit.log") `
    -RedirectStandardError (Join-Path $logDir "livekit.err.log")

# 2. Token mint + widget host.
# CALLIN_HOOK_REGISTER=0 unless the caller already set it: the station keeps
# ONE webhook row per id, so a dev boot beside a real deployment silently
# re-points the live instance's pushes at this PC (it happened, 2026-08-18 —
# the live ducking degraded with nothing logged). A dev stack runs fine on
# the poll fallback; export CALLIN_HOOK_REGISTER=1 first when the webhook
# code itself is what you are testing — or when THIS is your only instance
# (a Windows-local production wants its pushes: set it 1 in .env).
if (-not $env:CALLIN_HOOK_REGISTER) { $env:CALLIN_HOOK_REGISTER = "0" }
Start-Process -FilePath $python -ArgumentList "token_server.py" `
    -WorkingDirectory (Join-Path $root "agent-worker")

Start-Sleep -Seconds 2

# 3. Agent worker. "dev" gives hot reload; use "start" for production.
Start-Process -FilePath $python -ArgumentList "main.py", "dev" `
    -WorkingDirectory (Join-Path $root "agent-worker")

Write-Host ""
Write-Host "  LiveKit    ws://localhost:7880"
Write-Host "  Call page  http://localhost:8100" -ForegroundColor Green
Write-Host ""
Write-Host "  Stop with: .\run-local.ps1 -Stop"
