param(
  [string]$PgBinDir = "",
  [string]$RedisServerPath = "",
  [ValidateSet("dev", "start")]
  [string]$DashboardMode = "dev",
  [int]$ApiPort = 8000,
  [int]$DashboardPort = 3000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$DashboardDir = Join-Path $RepoRoot "dashboard"
$StackDir = Join-Path $RepoRoot ".tmp\local-stack"
$LogsDir = Join-Path $StackDir ("logs-" + (Get-Date -Format "yyyyMMdd_HHmmss"))
$StateFile = Join-Path $StackDir "state.json"

function Resolve-Python {
  $candidates = @(
    (Join-Path $RepoRoot ".venv\Scripts\python.exe"),
    (Get-Command python -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
  )
  return $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
}

function Resolve-Npm {
  $cmd = Get-Command npm -ErrorAction Stop
  return $cmd.Source
}

function Test-HttpReady([string]$Url, [int]$Attempts = 30, [int]$DelaySeconds = 2) {
  for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
    try {
      Invoke-WebRequest -Uri $Url -TimeoutSec 5 | Out-Null
      return $true
    }
    catch {
      if ($attempt -ge $Attempts) {
        break
      }
      Start-Sleep -Seconds $DelaySeconds
    }
  }
  return $false
}

function Stop-ProcessByIdSafe([int]$ProcessId, [string]$Name) {
  if ($ProcessId -le 0) {
    return
  }
  try {
    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -ne $proc) {
      Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
      Write-Host "[OK] Stopped $Name (pid $ProcessId)"
    }
  }
  catch {
    Write-Host "[WARN] Could not stop $Name (pid $ProcessId): $($_.Exception.Message)"
  }
}

function Invoke-LocalDatabaseShutdown {
  param(
    [string]$PgBinDir,
    [string]$RedisServerPath
  )

  $pgCtl = $null
  $redisCli = $null

  if ($PgBinDir) {
    $candidate = Join-Path $PgBinDir "pg_ctl"
    if (Test-Path $candidate) {
      $pgCtl = $candidate
    }
    elseif (Test-Path "$candidate.exe") {
      $pgCtl = "$candidate.exe"
    }
  }
  if (-not $pgCtl) {
    $cmd = Get-Command pg_ctl -ErrorAction SilentlyContinue
    if ($cmd) {
      $pgCtl = $cmd.Source
    }
  }

  if ($RedisServerPath) {
    if (Test-Path $RedisServerPath) {
      $redisCli = Join-Path (Split-Path -Parent $RedisServerPath) "redis-cli"
      if (-not (Test-Path $redisCli)) {
        $redisCli = "$redisCli.exe"
      }
    }
  }
  if (-not $redisCli) {
    $cmd = Get-Command redis-cli -ErrorAction SilentlyContinue
    if ($cmd) {
      $redisCli = $cmd.Source
    }
  }

  if ($redisCli) {
    try {
      & $redisCli -p 6379 shutdown nosave | Out-Null
      Write-Host "[OK] Redis shutdown requested"
    }
    catch {
      Write-Host "[WARN] Redis shutdown skipped: $($_.Exception.Message)"
    }
  }

  if ($pgCtl) {
    try {
      & $pgCtl -D (Join-Path $RepoRoot ".local\postgres-data\data") stop -m fast | Out-Null
      Write-Host "[OK] Postgres shutdown requested"
    }
    catch {
      Write-Host "[WARN] Postgres shutdown skipped: $($_.Exception.Message)"
    }
  }
}

if (-not (Test-Path (Join-Path $RepoRoot ".env"))) {
  & (Join-Path $PSScriptRoot "Create-LocalEnv.ps1")
}

New-Item -ItemType Directory -Force -Path $StackDir | Out-Null
New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null

$python = Resolve-Python
if (-not $python) {
  throw "Local Python runtime not found. Use .venv\\Scripts\\python.exe or install Python in PATH."
}

$npm = Resolve-Npm

$apiLogOut = Join-Path $LogsDir "api.out.log"
$apiLogErr = Join-Path $LogsDir "api.err.log"
$agentsLogOut = Join-Path $LogsDir "agents.out.log"
$agentsLogErr = Join-Path $LogsDir "agents.err.log"
$dashboardLogOut = Join-Path $LogsDir "dashboard.out.log"
$dashboardLogErr = Join-Path $LogsDir "dashboard.err.log"

$processes = @()

try {
  & (Join-Path $PSScriptRoot "Start-LocalPostgres.ps1") -PgBinDir $PgBinDir
  & (Join-Path $PSScriptRoot "Start-LocalRedis.ps1") -RedisServerPath $RedisServerPath

  $api = Start-Process -FilePath $python -WorkingDirectory $RepoRoot -ArgumentList @(
    "-m",
    "uvicorn",
    "api.main:app",
    "--host",
    "127.0.0.1",
    "--port",
    $ApiPort.ToString(),
    "--reload"
  ) -PassThru -RedirectStandardOutput $apiLogOut -RedirectStandardError $apiLogErr
  $processes += [pscustomobject]@{ name = "api"; pid = $api.Id }

  $agents = Start-Process -FilePath $python -WorkingDirectory $RepoRoot -ArgumentList @(
    "run_agents.py"
  ) -PassThru -RedirectStandardOutput $agentsLogOut -RedirectStandardError $agentsLogErr
  $processes += [pscustomobject]@{ name = "agents"; pid = $agents.Id }

  $env:NEXT_PUBLIC_API_URL = "http://localhost:$ApiPort"
  if ($DashboardMode -eq "dev") {
    $dashboardArgs = @(
      "run",
      "dev",
      "--",
      "--hostname",
      "127.0.0.1",
      "--port",
      $DashboardPort.ToString()
    )
  }
  else {
    $dashboardArgs = @("run", "start", "--", "--hostname", "127.0.0.1", "--port", $DashboardPort.ToString())
  }

  $dashboard = Start-Process -FilePath $npm -WorkingDirectory $DashboardDir -ArgumentList $dashboardArgs -PassThru -RedirectStandardOutput $dashboardLogOut -RedirectStandardError $dashboardLogErr
  $processes += [pscustomobject]@{ name = "dashboard"; pid = $dashboard.Id }

  $state = [ordered]@{
    started_at = (Get-Date).ToString("o")
    repo_root = $RepoRoot
    api_port = $ApiPort
    dashboard_port = $DashboardPort
    dashboard_mode = $DashboardMode
    processes = @(
      @{"name" = "api"; "pid" = $api.Id; "stdout" = $apiLogOut; "stderr" = $apiLogErr},
      @{"name" = "agents"; "pid" = $agents.Id; "stdout" = $agentsLogOut; "stderr" = $agentsLogErr},
      @{"name" = "dashboard"; "pid" = $dashboard.Id; "stdout" = $dashboardLogOut; "stderr" = $dashboardLogErr}
    )
    logs_dir = $LogsDir
  }
  $state | ConvertTo-Json -Depth 10 | Set-Content -Path $StateFile -Encoding UTF8

  Write-Host "[...] Waiting for API and dashboard to become ready"

  if (-not (Test-HttpReady -Url "http://127.0.0.1:$ApiPort/healthz" -Attempts 30 -DelaySeconds 2)) {
    throw "API health check failed"
  }

  if (-not (Test-HttpReady -Url "http://127.0.0.1:$DashboardPort" -Attempts 30 -DelaySeconds 2)) {
    throw "Dashboard health check failed"
  }

  Write-Host "[OK] Local stack started"
  Write-Host "[OK] API: http://127.0.0.1:$ApiPort/healthz"
  Write-Host "[OK] Dashboard: http://127.0.0.1:$DashboardPort"
  Write-Host "[OK] Logs: $LogsDir"
}
catch {
  Write-Host "[WARN] Startup failed, attempting cleanup"
  [array]::Reverse($processes)
  foreach ($process in $processes) {
    Stop-ProcessByIdSafe -ProcessId ([int]$process.pid) -Name ([string]$process.name)
  }
  Invoke-LocalDatabaseShutdown -PgBinDir $PgBinDir -RedisServerPath $RedisServerPath
  Remove-Item $StateFile -Force -ErrorAction SilentlyContinue
  throw
}
