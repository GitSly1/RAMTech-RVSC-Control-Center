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
    if ($py) { $PythonExe = $py.Source; $PythonPrefix = @("-3") }
}
if (-not $PythonExe) { throw "Python was not found." }

& $PythonExe @PythonPrefix -m controller.runtime_preflight

Write-Host ""
Write-Host "DANIEL HEALTH"
Write-Host "------------------------------------------------------------------------"
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8768/health" -Method Get -TimeoutSec 2
    $health | ConvertTo-Json -Depth 6
} catch {
    Write-Host "Daniel DEV-001 is OFFLINE or unreachable at 127.0.0.1:8768."
}
