$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $Root ".rvsc\daniel.pid"

if (Test-Path $pidFile) {
    $pidValue = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($pidValue -and ($pidValue -match '^\d+$')) {
        $process = Get-Process -Id ([int]$pidValue) -ErrorAction SilentlyContinue
        if ($process) {
            Stop-Process -Id $process.Id -Force
            Write-Host "Daniel DEV-001 stopped. PID $($process.Id)"
        } else {
            Write-Host "Recorded Daniel process is no longer running."
        }
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "No RVSC-managed Daniel PID file was found."
}

try {
    Invoke-RestMethod -Uri "http://127.0.0.1:8768/health" -Method Get -TimeoutSec 1 | Out-Null
    Write-Host "WARNING: A service is still responding on port 8768. It may have been started outside START_RVSC.ps1."
    exit 2
} catch {
    Write-Host "RVSC local Daniel endpoint is offline."
    exit 0
}
