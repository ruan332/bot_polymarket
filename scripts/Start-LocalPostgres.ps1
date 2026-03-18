param(
  [string]$PgBinDir = "",
  [string]$DataDir = ".local\postgres-data",
  [int]$Port = 5432,
  [string]$DbName = "trading",
  [string]$DbUser = "trading",
  [string]$DbPassword = "trading"
)

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

$pgCtl = Resolve-Binary "pg_ctl" $PgBinDir
$initDb = Resolve-Binary "initdb" $PgBinDir
$psql = Resolve-Binary "psql" $PgBinDir

if (-not $pgCtl -or -not $initDb -or -not $psql) {
  throw "Postgres binaries not found. Install PostgreSQL and optionally rerun with -PgBinDir 'C:\Program Files\PostgreSQL\<version>\bin'."
}

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
if (-not (Test-Path (Join-Path $DataDir "PG_VERSION"))) {
  $pwFile = Join-Path $DataDir "pgpass.txt"
  Set-Content -Path $pwFile -Value $DbPassword -Encoding ASCII
  & $initDb -D $DataDir -U $DbUser --pwfile=$pwFile
}

& $pgCtl -D $DataDir -o "-p $Port" -l (Join-Path $DataDir "postgres.log") start
Start-Sleep -Seconds 3

$env:PGPASSWORD = $DbPassword
& $psql -h localhost -p $Port -U $DbUser -d postgres -c "SELECT 1;" | Out-Null
$dbExists = & $psql -h localhost -p $Port -U $DbUser -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname='$DbName';"
if (($dbExists | Out-String).Trim() -ne "1") {
  & $psql -h localhost -p $Port -U $DbUser -d postgres -c "CREATE DATABASE $DbName;"
}
Write-Host "Postgres is running on localhost:$Port"
