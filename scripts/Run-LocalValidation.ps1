Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $RepoRoot ".tools\python312\python.exe"
$DashboardDir = Join-Path $RepoRoot "dashboard"
$TempDir = Join-Path $RepoRoot ".tmp\shell"
$PytestTempDir = Join-Path $RepoRoot (".tmp\pytest-run-" + [guid]::NewGuid().ToString("N"))

if (-not (Test-Path $PythonExe)) {
  throw "Local Python runtime not found at $PythonExe"
}

New-Item -ItemType Directory -Force $TempDir | Out-Null
New-Item -ItemType Directory -Force $PytestTempDir | Out-Null
$env:TMP = $TempDir
$env:TEMP = $TempDir

Push-Location $RepoRoot
try {
  & $PythonExe -m pytest -q --basetemp $PytestTempDir
  & $PythonExe -m compileall api core tests scripts run_agents.py
  Push-Location $DashboardDir
  try {
    npm run build
  }
  finally {
    Pop-Location
  }
}
finally {
  Pop-Location
}
