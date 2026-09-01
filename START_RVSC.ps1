$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$PythonExe = $null
$PythonPrefix = @()
$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) {
    $PythonExe = $python.Source
} else {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $PythonExe = $py.Source
        $PythonPrefix = @("-3")
    }
}
if (-not $PythonExe) {
    throw "Python was not found. Install Python 3 or add python/py to PATH."
}

Write-Host ""
Write-Host "RVSC PORTABLE STARTUP"
Write-Host "Project root: $Root"
Write-Host "Python: $PythonExe"
Write-Host ""

& $PythonExe @PythonPrefix -m controller.runtime_preflight
$preflightExit = $LASTEXITCODE
if ($preflightExit -ne 0) {
    Write-Host ""
    Write-Host "RVSC startup blocked by preflight. Correct the FAIL item(s) above and rerun START_RVSC.ps1."
    exit $preflightExit
}

$healthUrl = "http://127.0.0.1:8768/health"
$alreadyRunning = $false
try {
    $health = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 2
    if ($health.worker -eq "DEV-001") { $alreadyRunning = $true }
} catch { }

$stateDir = Join-Path $Root ".rvsc"
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
$pidFile = Join-Path $stateDir "daniel.pid"

if (-not $alreadyRunning) {
    $args = @($PythonPrefix + @("-m", "controller.daniel_multi_mission_host"))
    $process = Start-Process -FilePath $PythonExe -ArgumentList $args -WorkingDirectory $Root -PassThru -WindowStyle Hidden
    Set-Content -Path $pidFile -Value $process.Id -Encoding ascii

    $ready = $false
    foreach ($attempt in 1..20) {
        Start-Sleep -Milliseconds 500
        try {
            $health = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 2
            if ($health.worker -eq "DEV-001") { $ready = $true; break }
        } catch { }
    }
    if (-not $ready) {
        Write-Host "Daniel failed to become healthy at $healthUrl."
        if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
        exit 3
    }
    Write-Host "Daniel DEV-001 started. PID $($process.Id)"
} else {
    Write-Host "Daniel DEV-001 is already running; startup will not create a duplicate worker."
}

Write-Host "Opening RVSC Live Operations Console. Press Ctrl+C to leave the console; Daniel remains available until STOP_RVSC.ps1 is run."
Write-Host ""
& $PythonExe @PythonPrefix -m controller.ops_console
exit $LASTEXITCODE
