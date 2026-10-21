param([switch]$FailureRecovery)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path -Parent $PSScriptRoot)
function Assert-DockerSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed (exit $LASTEXITCODE)" }
}
try {
    & docker compose config --quiet
    Assert-DockerSuccess 'Compose validation'
    & docker compose exec -T nginx nginx -t
    Assert-DockerSuccess 'NGINX validation'
    & docker build -f tests/Dockerfile -t cloudguard-tests .
    Assert-DockerSuccess 'Test image build'
    & docker run --rm cloudguard-tests
    Assert-DockerSuccess 'Unit tests'
    & docker run --rm --network cloudguard_cloudguard cloudguard-tests python tests/live_check.py --expect healthy --requests 40
    Assert-DockerSuccess 'Live smoke tests'
    if ($FailureRecovery) {
        try {
            & "$PSScriptRoot\replica.ps1" -Action stop -Service app1
            & docker run --rm --network cloudguard_cloudguard cloudguard-tests python tests/live_check.py --expect degraded --requests 40
            Assert-DockerSuccess 'One-replica failover'
        } finally {
            & "$PSScriptRoot\replica.ps1" -Action start -Service app1
        }
        & docker run --rm --network cloudguard_cloudguard cloudguard-tests python tests/live_check.py --expect healthy --requests 40
        Assert-DockerSuccess 'Recovery tests'
    }
    Write-Host 'CloudGuard verification passed.'
} finally {
    Pop-Location
}
