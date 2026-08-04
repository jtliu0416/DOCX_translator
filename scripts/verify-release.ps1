param(
    [Parameter(Mandatory = $true)]
    [Alias('WebUiOrigin')]
    [string]$WebUiOrigins
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$previousOrigins = $env:VITE_WEBUI_ORIGINS
$origins = @(
    $WebUiOrigins.Split(',') |
        ForEach-Object { $_.Trim().TrimEnd('/') } |
        Where-Object { $_ }
)

if ($origins.Count -eq 0) {
    throw 'At least one WebUI origin is required.'
}
foreach ($origin in $origins) {
    if ($origin -notmatch '^https?://[A-Za-z0-9._:-]+$') {
        throw "Invalid WebUI origin: $origin"
    }
}
$normalizedOrigins = $origins -join ','

try {
    Write-Host 'Running backend tests...'
    & python -m unittest discover -s (Join-Path $repoRoot 'backend\tests')
    if ($LASTEXITCODE -ne 0) {
        throw "Backend tests failed with exit code $LASTEXITCODE"
    }

    Write-Host 'Installing locked frontend dependencies...'
    Push-Location (Join-Path $repoRoot 'frontend')
    try {
        & npm ci
        if ($LASTEXITCODE -ne 0) {
            throw "npm ci failed with exit code $LASTEXITCODE"
        }

        $env:VITE_WEBUI_ORIGINS = $normalizedOrigins
        Write-Host 'Building the production frontend...'
        & npm run build
        if ($LASTEXITCODE -ne 0) {
            throw "Frontend build failed with exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }

    Write-Host 'Release verification passed.' -ForegroundColor Green
} finally {
    $env:VITE_WEBUI_ORIGINS = $previousOrigins
}
