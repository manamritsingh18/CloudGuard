param(
    [Parameter(Mandatory=$true)][ValidateSet('stop','start','crash')][string]$Action,
    [ValidateSet('app1','app2')][string]$Service = 'app1'
)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    $target = if ($Service -eq 'app1') { 'app-1' } else { 'app-2' }
    & docker compose exec -T monitor python audit.py $Action $target requested
    if ($LASTEXITCODE -ne 0) { throw 'Cannot write the audit record. Ensure the monitor is running.' }
    if ($Action -eq 'crash') {
        # Docker init supervises Uvicorn. Kill its child, not the host or another container.
        & docker compose exec -T $Service python -c "import os,signal,pathlib; pid=int(pathlib.Path('/proc/1/task/1/children').read_text().split()[0]); os.kill(pid,signal.SIGKILL)"
    } else {
        & docker compose $Action $Service
    }
    $actionExit = $LASTEXITCODE
    $outcome = if ($actionExit -eq 0 -or ($Action -eq 'crash' -and $actionExit -eq 137)) { 'succeeded' } else { 'failed' }
    & docker compose exec -T monitor python audit.py $Action $target $outcome
    if ($LASTEXITCODE -ne 0) { throw 'Action executed, but completion audit failed. Inspect container status.' }
    if ($outcome -eq 'failed') { throw "Docker action failed with exit code $actionExit" }
    Write-Host "Action $Action completed for $target. Check the dashboard for the next recorded probe."
} finally {
    Pop-Location
}
