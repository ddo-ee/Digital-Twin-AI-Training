param(
    [string]$VenvName = ".venv",
    [switch]$SkipPipUpgrade,
    [switch]$SkipCheck
)

$ErrorActionPreference = "Stop"

$projectRoot = (Get-Location).Path
$venvPath = Join-Path $projectRoot $VenvName
$requirementsPath = Join-Path $projectRoot "requirements.txt"
$deployCheckPath = Join-Path $projectRoot "deploy_check.py"
$venvPython = Join-Path $venvPath "Scripts\python.exe"

Write-Host "Project folder: $projectRoot"

if (-not (Test-Path $requirementsPath)) {
    throw "requirements.txt was not found in $projectRoot"
}

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment at $venvPath"
    python -m venv $VenvName
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the virtual environment."
    }
} else {
    Write-Host "Using existing virtual environment at $venvPath"
}

if (-not $SkipPipUpgrade) {
    Write-Host "Upgrading pip inside the virtual environment"
    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to upgrade pip."
    }
}

Write-Host "Installing dependencies from requirements.txt"
& $venvPython -m pip install -r $requirementsPath
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install project dependencies."
}

if (-not $SkipCheck -and (Test-Path $deployCheckPath)) {
    Write-Host "Running deployment readiness check"
    & $venvPython $deployCheckPath
    if ($LASTEXITCODE -ne 0) {
        throw "Deployment readiness check failed."
    }
}

Write-Host ""
Write-Host "Deployment environment is ready in $venvPath"
Write-Host "Run the app with:"
Write-Host "  .\$VenvName\Scripts\python.exe cctv_manager.py"
