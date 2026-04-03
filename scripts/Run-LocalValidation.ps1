param(
  [switch]$WithLocalStack,
  [string]$PgBinDir = "",
  [string]$RedisServerPath = "",
  [ValidateSet("dev", "start")]
  [string]$DashboardMode = "dev"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$PythonCandidates = @(
  (Join-Path $RepoRoot ".venv\Scripts\python.exe"),
  (Get-Command python -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
)
$DashboardDir = Join-Path $RepoRoot "dashboard"
$TempDir = Join-Path $RepoRoot ".tmp\shell"
$PytestTempDir = Join-Path $RepoRoot (".tmp\pytest-run-" + [guid]::NewGuid().ToString("N"))
$StackStarted = $false

$PythonExe = $PythonCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $PythonExe) {
  throw "Local Python runtime not found. Use .venv\\Scripts\\python.exe or install Python in PATH."
}

New-Item -ItemType Directory -Force $TempDir | Out-Null
New-Item -ItemType Directory -Force $PytestTempDir | Out-Null
$env:TMP = $TempDir
$env:TEMP = $TempDir

Push-Location $RepoRoot
try {
  if ($WithLocalStack) {
    & "$PSScriptRoot\Start-LocalStack.ps1" -PgBinDir $PgBinDir -RedisServerPath $RedisServerPath -DashboardMode $DashboardMode
    $StackStarted = $true
  }

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
  if ($StackStarted) {
    & "$PSScriptRoot\Stop-LocalStack.ps1" -PgBinDir $PgBinDir -RedisServerPath $RedisServerPath
  }
  Pop-Location
}
