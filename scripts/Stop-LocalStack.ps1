param(
  [string]$PgBinDir = "",
  [string]$RedisServerPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$StackDir = Join-Path $RepoRoot ".tmp\local-stack"
$StateFile = Join-Path $StackDir "state.json"

function Resolve-Binary([string]$name, [string]$binDir) {
  if ($binDir) {
    $candidate = Join-Path $binDir $name
    if (Test-Path $candidate) { return $candidate }
    if (Test-Path "$candidate.exe") { return "$candidate.exe" }
  }
  $cmd = Get-Command $name -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  return $null
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

function Stop-LocalDatabase {
  param(
    [string]$PgBinDir,
    [string]$RedisServerPath
  )

  $pgCtl = Resolve-Binary "pg_ctl" $PgBinDir
  $redisCli = $null

  if ($RedisServerPath) {
    $redisCandidate = Join-Path (Split-Path -Parent $RedisServerPath) "redis-cli"
    if (Test-Path $redisCandidate) {
      $redisCli = $redisCandidate
    }
    elseif (Test-Path "$redisCandidate.exe") {
      $redisCli = "$redisCandidate.exe"
    }
  }
  if (-not $redisCli) {
    $redisCli = Resolve-Binary "redis-cli" ""
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
    $dataDir = Join-Path $RepoRoot ".local\postgres-data\data"
    if (Test-Path $dataDir) {
      try {
        & $pgCtl -D $dataDir stop -m fast | Out-Null
        Write-Host "[OK] Postgres shutdown requested"
      }
      catch {
        Write-Host "[WARN] Postgres shutdown skipped: $($_.Exception.Message)"
      }
    }
  }
}

if (Test-Path $StateFile) {
  $state = Get-Content $StateFile -Raw | ConvertFrom-Json
  if ($state.processes) {
    $processes = @($state.processes)
    [array]::Reverse($processes)
    foreach ($process in $processes) {
      Stop-ProcessByIdSafe -ProcessId ([int]$process.pid) -Name ([string]$process.name)
    }
  }
  Stop-LocalDatabase -PgBinDir $PgBinDir -RedisServerPath $RedisServerPath
  Remove-Item $StateFile -Force -ErrorAction SilentlyContinue
  Write-Host "[OK] Local stack stopped"
}
else {
  Stop-LocalDatabase -PgBinDir $PgBinDir -RedisServerPath $RedisServerPath
  Write-Host "[WARN] No local stack state file found; database shutdown was attempted only"
}
