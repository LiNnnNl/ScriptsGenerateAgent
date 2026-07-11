param(
    [string]$TunnelConfig = "E:\Backend\cloudflare-tunnel.yml"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Test-LocalService {
    param([int]$Port)

    try {
        $connection = Test-NetConnection -ComputerName "127.0.0.1" -Port $Port -WarningAction SilentlyContinue
        return $connection.TcpTestSucceeded
    }
    catch {
        return $false
    }
}

Write-Host "================================================="
Write-Host " ScriptAgent public service"
Write-Host "================================================="

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv was not found. Install uv first, then run this script again."
}

if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    throw "cloudflared was not found. Install Cloudflare Tunnel first, then run this script again."
}

if (-not (Test-Path -LiteralPath $TunnelConfig)) {
    throw "Tunnel config was not found: $TunnelConfig"
}

if (Test-LocalService -Port 5001) {
    Write-Host "==> Reuse ScriptAgent on http://127.0.0.1:5001"
}
else {
    Write-Host "==> Start ScriptAgent on http://127.0.0.1:5001"
    $backendCommand = "Set-Location -LiteralPath '$projectRoot'; uv run python backend/app.py"
    Start-Process powershell.exe -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $backendCommand | Out-Null

    $ready = $false
    for ($attempt = 1; $attempt -le 20; $attempt++) {
        Start-Sleep -Seconds 1
        if (Test-LocalService -Port 5001) {
            $ready = $true
            break
        }
    }

    if (-not $ready) {
        throw "ScriptAgent did not start on port 5001. Check the backend PowerShell window."
    }
}

Write-Host "==> Validate Cloudflare Tunnel configuration"
& cloudflared tunnel --config $TunnelConfig ingress validate
if ($LASTEXITCODE -ne 0) {
    throw "Cloudflare Tunnel configuration validation failed."
}

Write-Host ""
Write-Host "Public ScriptAgent: https://couvzob.kdns.fr/script/"
Write-Host "Keep this window open to keep the tunnel running. Press Ctrl+C to stop only this tunnel."
Write-Host ""

& cloudflared tunnel --config $TunnelConfig run
