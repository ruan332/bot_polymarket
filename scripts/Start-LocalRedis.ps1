param(
  [string]$RedisServerPath = "",
  [string]$DataDir = ".local\redis-data",
  [int]$Port = 6379
)

function Resolve-Binary([string]$name, [string]$explicitPath) {
  if ($explicitPath) {
    if (Test-Path $explicitPath) { return $explicitPath }
  }
  $cmd = Get-Command $name -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  return $null
}

$redisServer = Resolve-Binary "redis-server" $RedisServerPath
if (-not $redisServer) {
  throw "redis-server not found. Install Redis for Windows/WSL and rerun with -RedisServerPath if needed."
}

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
$configPath = Join-Path $DataDir "redis.local.conf"
@"
port $Port
dir $((Resolve-Path $DataDir).Path)
save 60 1
appendonly yes
"@ | Set-Content -Path $configPath -Encoding UTF8

Start-Process -FilePath $redisServer -ArgumentList $configPath -WindowStyle Hidden | Out-Null
Start-Sleep -Seconds 2
Write-Host "Redis is running on localhost:$Port"
