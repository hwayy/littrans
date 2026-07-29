param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$PluginRoot = Join-Path $RepoRoot "plugins\literature-translation"
$VenvRoot = Join-Path $RepoRoot ".venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
$PytestTemp = Join-Path $RepoRoot "tmp\release-checks\pytest"
$SourcePath = Join-Path $PluginRoot "src"

New-Item -ItemType Directory -Force (Split-Path -Parent $PytestTemp) | Out-Null

if (-not (Test-Path -LiteralPath $VenvPython)) {
    & $Python -m venv $VenvRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the repository virtual environment."
    }
}

& $VenvPython -c "import hatchling"
if ($LASTEXITCODE -ne 0) {
    & $VenvPython -m pip install --quiet --disable-pip-version-check "hatchling>=1.25"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install the LitTrans build backend."
    }
}

& $VenvPython -m pip install --quiet --disable-pip-version-check --no-build-isolation "$PluginRoot[dev]"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install LitTrans development dependencies."
}

& $VenvPython (Join-Path $PSScriptRoot "validate_release.py")
if ($LASTEXITCODE -ne 0) {
    throw "Release metadata validation failed."
}

$PreviousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = if ($PreviousPythonPath) {
    "$SourcePath$([IO.Path]::PathSeparator)$PreviousPythonPath"
}
else {
    $SourcePath
}

Push-Location $PluginRoot
try {
    & $VenvPython -m ruff check .
    if ($LASTEXITCODE -ne 0) {
        throw "Ruff validation failed."
    }

    & $VenvPython -m mypy src\littrans
    if ($LASTEXITCODE -ne 0) {
        throw "Mypy validation failed."
    }

    & $VenvPython -m pytest --basetemp $PytestTemp
    if ($LASTEXITCODE -ne 0) {
        throw "Pytest validation failed."
    }

    & $VenvPython scripts\littrans.py doctor
    if ($LASTEXITCODE -ne 0) {
        throw "LitTrans doctor failed."
    }
}
finally {
    Pop-Location
    $env:PYTHONPATH = $PreviousPythonPath
}

Write-Host "LitTrans release checks passed."
